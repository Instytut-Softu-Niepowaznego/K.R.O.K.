"""Raport wykonalności: czy plan w ogóle da się ułożyć (#10).

Każde sprawdzenie to warunek konieczny. Problem `error` oznacza, że żaden plan nie spełni reguły twardej `ruleId`
(docs/etap1/kryteria.md), więc solver nie musi tego odkrywać sam. Brak błędów nie dowodzi, że plan istnieje:
rozstrzyga to dopiero solver (#17). Problem `warning` to ryzyko albo nietypowe dane, ale nie przeszkoda.
"""

import time
from collections import defaultdict
from typing import Literal

from pydantic import Field

from krok.model import Dataset, DayOverride, Holiday, Rector
from krok.model.base import Model

from .measures import day_window
from .state import WEEKS, ClassInfo, EnvironmentState, build_state

ROOM_WARNING = 0.85  # obłożenie sal powyżej tej wartości → ostrzeżenie
LECTURER_WARNING = 0.9  # obciążenie kompetencji powyżej tej wartości → ostrzeżenie


class Problem(Model):
  """Jeden wykryty problem z danymi wejściowymi."""

  code: str
  rule_id: str | None = None  # reguła twarda, którą każdy plan złamie (dla `error`)
  severity: Literal["error", "warning"]
  message: str
  class_ids: list[str] = []
  ids: list[str] = []  # inne encje, których problem dotyczy


class RoomBalance(Model):
  """Czas sal danego typu w tygodniu: potrzebny vs dostępny (minuty)."""

  room_type: str
  study_mode: str
  rooms: int
  demand_minutes: float
  supply_minutes: int
  utilization: float | None


class CompetenceBalance(Model):
  """Zapotrzebowanie na kompetencję vs suma limitów i dostępności prowadzących, którzy ją mają."""

  competence: str
  lecturers: int
  demand_units: float
  limit_units: int
  demand_minutes: float
  available_minutes: int


class GroupBalance(Model):
  """Plan studenta grupy-liścia w gorszym tygodniu: potrzebny czas vs czas w dniach zajęć."""

  group_id: str
  week: str
  units: int
  max_units: int | None
  required_minutes: int
  available_minutes: int


class Balance(Model):
  rooms: list[RoomBalance]
  competences: list[CompetenceBalance]
  groups: list[GroupBalance]  # najciaśniejsze grupy-liście, od najbardziej obciążonej


class FeasibilityReport(Model):
  """Raport wykonalności. `verdict`: "infeasible" (są błędy) albo "no-obstacles-found" (warunki konieczne spełnione)."""

  verdict: Literal["infeasible", "no-obstacles-found"]
  errors: int
  warnings: int
  problems: list[Problem]
  balance: Balance
  travel_minutes: dict[str, dict[str, float]]
  rooms_by_capacity: dict[str, dict[str, int]]  # typ → pojemność → liczba sal
  counts: dict[str, int]
  compute_seconds: float = Field(ge=0)


def _rule_enabled(ds: Dataset, rule_id: str) -> bool:
  return any(r.id == rule_id and r.enabled for r in ds.config.rules)


def _max_units(ds: Dataset) -> int | None:
  rule = next((r for r in ds.config.rules if r.id == "max-jednostek-dziennie" and r.enabled), None)
  return rule.params.get("maxUnits") if rule else None


# Każde sprawdzenie zakłada, że obowiązują pewne reguły twarde. Jeśli któraś jest wyłączona, plan może ją łamać,
# więc wykryty problem przestaje być przeszkodą. Np. bez komplet-godzin plan może w ogóle pominąć zajęcia.
SCHEDULED = ("komplet-godzin",)


def _need(infos: list[ClassInfo], week: str, minutes: bool = False) -> float:
  """Zapotrzebowanie w tygodniu A lub B: jednostki albo minuty spotkań zajęć `every` i danego tygodnia."""
  return sum((i.meetings * i.meeting_minutes if minutes else i.cls.units_per_week) for i in infos if i.in_week(week))


class _Checker:
  def __init__(self, state: EnvironmentState):
    self.s = state
    self.ds = state.dataset
    self.grid = self.ds.config.time_grid
    self.problems: list[Problem] = []

  def add(self, code: str, severity: str, message: str, rule_id: str | None = None, class_ids=(), ids=(), assumes=()):
    if severity == "error":
      off = [r for r in (rule_id, *assumes) if r and not _rule_enabled(self.ds, r)]
      if off:
        severity, message = "warning", f"{message} (wyłączone reguły: {', '.join(off)}, więc to nie przeszkoda)"
    self.problems.append(Problem(code=code, rule_id=rule_id, severity=severity, message=message,
                                 class_ids=sorted(set(class_ids)), ids=sorted(set(ids))))

  # --- zajęcia: sala, prowadzący, długość ---

  def classes(self) -> None:
    lec_by_id = {lec.id: lec for lec in self.ds.lecturers}
    day_start, day_end = day_window(self.grid)
    max_units = _max_units(self.ds)
    no_comp, no_room, no_avail, too_long, too_many = (defaultdict(list) for _ in range(5))
    blocked_groups = set(self.s.full_semester_blocks["groups"])

    # Gdy typ-sali jest wyłączona, do pojemności liczy się każda sala (kryteria, pojemnosc-sali).
    any_type = not _rule_enabled(self.ds, "typ-sali")
    biggest_any = max((r.capacity for rooms in self.s.rooms_by_type.values() for r in rooms), default=0)
    for cid, info in self.s.classes.items():
      c, size = info.cls, info.group.size
      fits_room = biggest_any >= size if any_type else any(r.capacity >= size for r in self.s.rooms_by_type.get(c.room_type, []))
      if not fits_room:
        no_room[(c.room_type, c.group_id)].append(cid)
      if info.meeting_minutes > day_end - day_start:
        too_long[c.units_per_meeting].append(cid)
      if max_units is not None and c.units_per_meeting > max_units:
        too_many[c.units_per_meeting].append(cid)
      if c.group_id in blocked_groups:
        self.add("zablokowana-grupa", "error", f"Grupa {c.group_id} ma blokadę na cały semestr, a ma zajęcia {cid}",
                 "blokady-zasobow", [cid], [c.group_id], SCHEDULED)
      pinned = lec_by_id.get(c.lecturer_id) if c.lecturer_id else None
      if pinned and info.competence not in pinned.competences:
        self.add("przydzial-bez-kompetencji", "error", f"{cid} ma na sztywno {pinned.id}, który nie ma kompetencji "
                 f"{info.competence}", "kompetencja-prowadzacego", [cid], [pinned.id], SCHEDULED)
      if not [lid for lid in info.eligible if lid in lec_by_id]:
        no_comp[(info.competence, c.subject_id)].append(cid)
      elif not info.available:
        no_avail[tuple(info.eligible)].append(cid)

    for (comp, sid), cids in no_comp.items():
      self.add("brak-kompetencji", "error", f"Żaden prowadzący nie ma kompetencji {comp} (przedmiot {sid})",
               "kompetencja-prowadzacego", cids, [sid], SCHEDULED)
    for (room_type, gid), cids in no_room.items():
      have = self.s.rooms_by_type.get(room_type, [])
      biggest = f"największa ma {have[0].capacity} miejsc" if have else "brak dostępnych sal tego typu"
      self.add("brak-sali", "error", f"Grupa {gid} ({self.s.classes[cids[0]].group.size} os.) nie mieści się w żadnej sali "
               f"typu {room_type} ({biggest})", "pojemnosc-sali", cids, [gid], SCHEDULED)
    for eligible, cids in no_avail.items():
      info = self.s.classes[cids[0]]
      who = "prowadzący " + eligible[0] if len(eligible) == 1 else f"nikt z {len(eligible)} możliwych prowadzących"
      self.add("brak-dostepnego-prowadzacego", "error",
               f"{len(cids)} zajęć ({info.meeting_minutes} min, {info.study_mode}): {who} nie ma okna dostępności, "
               "w którym mieści się spotkanie", "dostepnosc-prowadzacego", cids, eligible,
               (*SCHEDULED, "kompetencja-prowadzacego", "godziny-dnia", "spojnosc-planu"))
    for units, cids in too_long.items():
      self.add("zajecia-dluzsze-niz-dzien", "error", f"Spotkanie {units} jednostek nie mieści się między dayStart a dayEnd",
               "godziny-dnia", cids, assumes=SCHEDULED)
    for units, cids in too_many.items():
      self.add("spotkanie-ponad-limit-dzienny", "error", f"Spotkanie {units} jednostek przekracza max-jednostek-dziennie",
               "max-jednostek-dziennie", cids, assumes=SCHEDULED)

  # --- prowadzący: przydziały na sztywno, pule, kompetencje ---

  def lecturers(self) -> list[CompetenceBalance]:
    lec_by_id = {lec.id: lec for lec in self.ds.lecturers}
    subjects = {s.id: s for s in self.ds.subjects}
    pinned, pools, by_comp = defaultdict(list), defaultdict(list), defaultdict(list)
    for info in self.s.classes.values():
      by_comp[info.competence].append(info)
      if info.cls.lecturer_id:
        pinned[info.cls.lecturer_id].append(info)
      elif subjects[info.cls.subject_id].pool(info.cls.form) is not None:
        pools[(info.cls.subject_id, info.cls.form)].append(info)
    pinned_rules = (*SCHEDULED, "spojnosc-planu")

    for lid, infos in pinned.items():
      lec = lec_by_id.get(lid)
      if lec is None:
        continue
      week = max(WEEKS, key=lambda w: _need(infos, w))
      if _need(infos, week) > lec.max_units_weekly:
        self.add("limit-prowadzacego", "error", f"{lid} ma na sztywno {_need(infos, week):g} jednostek w tygodniu {week}, "
                 f"a limit {lec.max_units_weekly}", "limit-godzin-prowadzacego", [i.cls.id for i in infos], [lid], pinned_rules)
      for mode in sorted({i.study_mode for i in infos}):
        in_mode = [i for i in infos if i.study_mode == mode]
        need = max(_need(in_mode, w, minutes=True) for w in WEEKS)
        have = self.s.available_minutes(lid, mode)
        if have and need > have:  # brak dostępności w ogóle zgłasza już brak-dostepnego-prowadzacego
          self.add("za-malo-czasu-prowadzacego", "error", f"{lid} ma na sztywno {need:g} min zajęć ({mode}), "
                   f"a dostępność {have} min", "dostepnosc-prowadzacego", [i.cls.id for i in infos], [lid],
                   (*pinned_rules, "kolizja-prowadzacych"))

    # Zajęcia z pulą może poprowadzić tylko ktoś z puli, więc ich zapotrzebowanie musi zmieścić się w limitach puli.
    for (sid, form), infos in pools.items():
      pool = infos[0].eligible
      limit = sum(lec_by_id[lid].max_units_weekly for lid in pool if lid in lec_by_id)
      week = max(WEEKS, key=lambda w: _need(infos, w))
      if _need(infos, week) > limit:
        self.add("limit-puli", "error", f"Pula {sid}/{form} ma łączny limit {limit} jednostek, a zajęcia wymagają "
                 f"{_need(infos, week):g} w tygodniu {week}", "limit-godzin-prowadzacego", [i.cls.id for i in infos],
                 [sid, *pool], (*SCHEDULED, "kompetencja-prowadzacego"))
      else:
        self._mode_limits(f"Pula {sid}/{form}", "limit-puli", infos, lec_by_id, [sid, *pool])
      for mode in sorted({i.study_mode for i in infos}):
        need = max(_need([i for i in infos if i.study_mode == mode], w, minutes=True) for w in WEEKS)
        have = sum(self.s.available_minutes(lid, mode) for lid in pool if lid in lec_by_id)
        if have and need > have:  # zerową dostępność zgłasza brak-dostepnego-prowadzacego
          self.add("za-malo-czasu-puli", "error", f"Pula {sid}/{form} ({mode}): zajęcia trwają {need:g} min w tygodniu, "
                   f"a prowadzący z puli są dostępni łącznie {have} min", "dostepnosc-prowadzacego",
                   [i.cls.id for i in infos if i.study_mode == mode], [sid, *pool],
                   (*SCHEDULED, "kompetencja-prowadzacego", "kolizja-prowadzacych"))

    # Zajęcia z kompetencją c może poprowadzić tylko ktoś z kompetencją c (także przydziały na sztywno, inaczej
    # przydzial-bez-kompetencji), więc suma ich limitów i dostępności musi pokryć zapotrzebowanie.
    comp_rules = (*SCHEDULED, "kompetencja-prowadzacego")
    balances = []
    for comp, infos in sorted(by_comp.items()):
      lecs = [lec for lec in self.ds.lecturers if comp in lec.competences]
      limit = sum(lec.max_units_weekly for lec in lecs)
      week = max(WEEKS, key=lambda w: _need(infos, w))
      need = _need(infos, week)
      modes = sorted({i.study_mode for i in infos})
      minutes = {m: max(_need([i for i in infos if i.study_mode == m], w, minutes=True) for w in WEEKS) for m in modes}
      avail = {m: sum(self.s.available_minutes(lec.id, m) for lec in lecs) for m in modes}
      balances.append(CompetenceBalance(competence=comp, lecturers=len(lecs), demand_units=need, limit_units=limit,
                                        demand_minutes=sum(minutes.values()), available_minutes=sum(avail.values())))
      if not lecs:
        continue  # zgłasza to brak-kompetencji
      if need > limit:
        self.add("limit-kompetencji", "error", f"Kompetencja {comp}: zajęcia wymagają {need:g} jednostek w tygodniu "
                 f"{week}, a suma limitów {len(lecs)} prowadzących to {limit}", "limit-godzin-prowadzacego",
                 [i.cls.id for i in infos], [comp], comp_rules)
      else:
        if need > LECTURER_WARNING * limit:
          self.add("wysokie-obciazenie-kompetencji", "warning", f"Kompetencja {comp} wykorzystuje {need / limit:.0%} limitów",
                   ids=[comp])
        self._mode_limits(f"Kompetencja {comp}", "limit-kompetencji", infos, lec_by_id, [comp])
      for m in modes:
        if avail[m] and minutes[m] > avail[m]:
          self.add("za-malo-czasu-kompetencji", "error", f"Kompetencja {comp} ({m}): zajęcia trwają {minutes[m]:g} min "
                   f"w tygodniu, a prowadzący z tą kompetencją są dostępni łącznie {avail[m]} min",
                   "dostepnosc-prowadzacego", [i.cls.id for i in infos if i.study_mode == m], [comp],
                   (*comp_rules, "kolizja-prowadzacych"))

    for lec in self.ds.lecturers:
      if not self.s.lecturer_availability.get(lec.id) and lec.id not in pinned:
        self.add("prowadzacy-bez-dostepnosci", "warning", f"{lec.id} nie ma żadnej dostępności", ids=[lec.id])
    return balances

  def _mode_limits(self, what: str, code: str, infos: list[ClassInfo], lec_by_id: dict, ids: list[str]) -> None:
    """Limit w każdym trybie osobno: zajęcia zaoczne może poprowadzić tylko ktoś, komu mieszczą się w weekend,
    więc łączny limit nie wystarczy, jeśli na osoby dostępne w danym trybie przypada go za mało."""
    for mode in sorted({i.study_mode for i in infos}):
      in_mode = [i for i in infos if i.study_mode == mode]
      able = sorted({lid for i in in_mode for lid in i.available if lid in lec_by_id})
      if not able:
        continue  # nikt nie może ich poprowadzić: zgłasza to brak-dostepnego-prowadzacego
      limit = sum(lec_by_id[lid].max_units_weekly for lid in able)
      week = max(WEEKS, key=lambda w: _need(in_mode, w))
      if _need(in_mode, week) > limit:
        self.add(code, "error", f"{what} ({mode}): zajęcia wymagają {_need(in_mode, week):g} jednostek w tygodniu {week}, "
                 f"a {len(able)} osób, które mogą je prowadzić w tym trybie, ma łączny limit {limit}",
                 "limit-godzin-prowadzacego", [i.cls.id for i in in_mode], [*ids, *able],
                 (*SCHEDULED, "kompetencja-prowadzacego", "dostepnosc-prowadzacego", "godziny-dnia", "spojnosc-planu"))

  # --- sale: czas sal per typ, tryb, tydzień i próg pojemności ---

  def rooms(self) -> list[RoomBalance]:
    by_key = defaultdict(list)  # (typ, tryb) → zajęcia
    for info in self.s.classes.values():
      by_key[(info.cls.room_type, info.study_mode)].append(info)
    check_types = _rule_enabled(self.ds, "typ-sali")  # bez typ-sali zajęcia mogą trafić do sal innego typu
    room_rules = (*SCHEDULED, "kolizja-sal", "typ-sali", "pojemnosc-sali", "godziny-dnia")

    balances = []
    for (room_type, mode), infos in sorted(by_key.items()):
      days = self.s.mode_days(mode)
      rooms = self.s.rooms_by_type.get(room_type, [])
      supply = {r.id: sum(e - s for d in days for s, e in self.s.room_availability[r.id].get(d, [])) for r in rooms}
      total_supply = sum(supply.values())
      week = max(WEEKS, key=lambda w: _need(infos, w, minutes=True))
      total_demand = _need(infos, week, minutes=True)
      balances.append(RoomBalance(room_type=room_type, study_mode=mode, rooms=len(rooms), demand_minutes=total_demand,
                                  supply_minutes=total_supply,
                                  utilization=round(total_demand / total_supply, 3) if total_supply else None))
      # Dla progu pojemności k: zajęcia grup ≥ k osób mogą trafić tylko do sal ≥ k miejsc.
      shortage = None
      for k in sorted({i.group.size for i in infos}) if check_types else []:
        big = [i for i in infos if i.group.size >= k]
        have = sum(supply[r.id] for r in rooms if r.capacity >= k)
        w = max(WEEKS, key=lambda w: _need(big, w, minutes=True))
        if have and _need(big, w, minutes=True) > have:
          shortage = (k, w, _need(big, w, minutes=True), have, big)
          break
      if shortage:
        k, w, need, have, big = shortage
        self.add("za-malo-sal", "error", f"Sale {room_type} z ≥ {k} miejscami ({mode}, tydzień {w}): potrzeba {need:g} min, "
                 f"dostępne {have} min", "komplet-godzin", [i.cls.id for i in big], [room_type], room_rules)
      elif total_supply and total_demand > ROOM_WARNING * total_supply:
        self.add("wysokie-oblozenie-sal", "warning", f"Sale {room_type} ({mode}) zajęte w {total_demand / total_supply:.0%}",
                 ids=[room_type])
    return balances

  # --- grupy: plan studenta w tygodniu ---

  def groups(self) -> list[GroupBalance]:
    by_group = defaultdict(list)
    for info in self.s.classes.values():
      by_group[info.cls.group_id].append(info)
    max_units = _max_units(self.ds)
    mode = {p.id: p.study_mode for p in self.ds.programs}
    group_rules = (*SCHEDULED, "kolizja-grup", "godziny-dnia")
    balances = []
    for leaf in self.s.leaf_groups:
      days = self.s.mode_days(mode[leaf.program_id])
      available = self.s.group_available_minutes(leaf.id, mode[leaf.program_id])
      cap_units = max_units * len(days) if max_units is not None else None
      worst = None
      for week in WEEKS:
        infos = [i for g in self.s.ancestors[leaf.id] for i in by_group[g] if i.in_week(week)]
        units = sum(i.cls.units_per_week for i in infos)
        required = sum(i.meetings * i.meeting_minutes for i in infos)
        row = GroupBalance(group_id=leaf.id, week=week, units=units, max_units=cap_units, required_minutes=required,
                           available_minutes=available)
        worst = row if worst is None or required > worst.required_minutes else worst
        ids = [i.cls.id for i in infos]
        if required > available:
          self.add("za-malo-czasu-grupy", "error", f"Plan studenta {leaf.id} (tydzień {week}) wymaga {required} min, "
                   f"a dni zajęć (po blokadach) dają {available} min", "komplet-godzin", ids, [leaf.id],
                   (*group_rules, "blokady-zasobow"))
        if cap_units is not None and units > cap_units:
          self.add("za-duzo-jednostek-grupy", "error", f"Plan studenta {leaf.id} (tydzień {week}) ma {units} jednostek, "
                   f"a max-jednostek-dziennie pozwala na {cap_units}", "max-jednostek-dziennie", ids, [leaf.id],
                   (*SCHEDULED, "godziny-dnia"))
      if worst:
        balances.append(worst)
    balances.sort(key=lambda b: -b.required_minutes / b.available_minutes if b.available_minutes else 0)
    return balances[:20]

  # --- kalendarz ---

  def calendar(self) -> None:
    cal = self.ds.calendar
    days_off = [e for e in cal.exceptions if isinstance(e, (Holiday, Rector)) and e.from_time is None]
    for e in cal.exceptions:
      if isinstance(e, DayOverride):
        clash = [h.id for h in days_off if h.from_ <= e.date <= h.to]
        if clash:
          self.add("odpracowanie-w-dniu-wolnym", "warning", f"{e.id} ({e.date}) wypada w dniu wolnym: zajęcia "
                   "i tak będą odwołane", ids=[e.id, *clash])


def analyze(ds: Dataset) -> tuple[EnvironmentState, FeasibilityReport]:
  """Buduje stan otoczenia i raport wykonalności."""
  started = time.perf_counter()
  state = build_state(ds)
  check = _Checker(state)
  check.classes()
  competences = check.lecturers()
  rooms = check.rooms()
  groups = check.groups()
  check.calendar()
  problems = sorted(check.problems, key=lambda p: (p.severity != "error", p.code, p.ids, p.class_ids))
  errors = sum(p.severity == "error" for p in problems)
  report = FeasibilityReport(
    verdict="infeasible" if errors else "no-obstacles-found", errors=errors, warnings=len(problems) - errors,
    problems=problems, balance=Balance(rooms=rooms, competences=competences, groups=groups), travel_minutes=state.travel,
    rooms_by_capacity=state.rooms_by_capacity(),
    counts={"classes": len(ds.classes), "groups": len(ds.groups), "leafGroups": len(state.leaf_groups),
            "lecturers": len(ds.lecturers), "rooms": len(ds.rooms), "usableRooms": sum(map(len, state.rooms_by_type.values()))},
    compute_seconds=round(time.perf_counter() - started, 3),
  )
  return state, report

