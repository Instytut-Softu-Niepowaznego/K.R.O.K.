"""Generator strumienia zdarzeń spójnego z danymi z #7.

Zdarzenia przychodzą jak w procesie Poissona (`events.per_week`), od tygodnia przed semestrem do jego końca.
Poprawne zdarzenia odwołują się tylko do encji z danych bazowych, więc zepsucie jednego zdarzenia
nie unieważnia kolejnych. Część zdarzeń (`events.error_rate`) jest celowo błędna; lista jest w manifeście.
"""

import datetime as dt
import random
from collections import Counter
from dataclasses import dataclass, field

from krok.model import EVENT_TYPES, Class, Dataset, Group, Rector, Subject, dump_event, parse_event, to_hhmm, to_minutes

from krok.generator.lecturers import AvailabilityGenerator
from krok.generator.settings import EventSpec, Preset, load_catalog

from .errors import ERROR_KINDS, inject_errors, to_line

ROOM_REASONS = ["awaria rzutnika", "awaria klimatyzacji", "zajęcie przez inną jednostkę", "zalanie", "awaria sieci"]
ABSENCE_REASONS = ["choroba", "wyjazd konferencyjny", "delegacja", "urlop okolicznościowy"]
ELECTIVES = [
  ("Przedmiot obieralny: Blockchain", "bezpieczenstwo"),
  ("Przedmiot obieralny: Internet rzeczy", "sieci"),
  ("Przedmiot obieralny: Programowanie gier", "programowanie"),
  ("Przedmiot obieralny: Analiza danych w Pythonie", "sztuczna-inteligencja"),
  ("Przedmiot obieralny: Systemy wbudowane", "elektronika"),
]


@dataclass
class EventStreamResult:
  lines: list[str]
  manifest: list[dict]
  meta: dict = field(default_factory=dict)


class _Builder:
  def __init__(self, ds: Dataset, preset: Preset, rng: random.Random):
    self.ds, self.spec, self.rng = ds, preset.events, rng
    self.preset = preset
    self.start, self.end = ds.calendar.semester_start, ds.calendar.semester_end
    self.avail = AvailabilityGenerator(preset, ds.config, rng)
    catalog = load_catalog(preset.catalog)
    self.external = {c.id for c in catalog.competence if c.external}
    self.groups = {g.id: g for g in ds.groups}
    self.taken_ids = ({g.id for g in ds.groups} | {c.id for c in ds.classes} | {s.id for s in ds.subjects}
                      | {e.id for e in ds.calendar.exceptions})
    self.day_start = to_minutes(ds.config.time_grid.day_start)
    self.day_end = to_minutes(ds.config.time_grid.day_end)
    self.sizes = {g.id: g.size for g in ds.groups}

  # --- czas ---

  def effective(self, ts: dt.datetime, notice: tuple[int, int] | None = None) -> dt.date:
    day = ts.date() + dt.timedelta(days=self.rng.randint(*(notice or self.spec.notice_days)))
    day = min(max(day, self.start), self.end)
    while day.weekday() >= 5 and day < self.end:  # większość zmian dotyczy dni zajęć dziennych
      day += dt.timedelta(days=1)
    return day

  def period(self, begin: dt.date) -> tuple[dt.date, dt.date, bool]:
    long = self.rng.random() < self.spec.long_share
    days = self.rng.randint(*(self.spec.long_days if long else self.spec.short_days))
    return begin, min(begin + dt.timedelta(days=days - 1), self.end), long

  # --- typy zdarzeń ---

  def roomUnavailable(self, ts):
    room = self.rng.choice(self.ds.rooms)
    begin, end, long = self.period(self.effective(ts))
    payload = {"roomId": room.id, "from": begin, "to": end, "reason": "remont" if long else self.rng.choice(ROOM_REASONS)}
    if not long and self.rng.random() < self.spec.partial_day_share:
      length = self.rng.randint(2, 4) * 60
      start = self.rng.randrange(self.day_start, max(self.day_start, self.day_end - length) + 1, 15)
      payload |= {"fromTime": to_hhmm(start), "toTime": to_hhmm(min(start + length, self.day_end))}
    return payload

  def lecturerAbsent(self, ts):
    lec = self.rng.choice([lec for lec in self.ds.lecturers if lec.availability])
    begin, end, long = self.period(self.effective(ts, (0, 3)))
    reason = "długie zwolnienie lekarskie" if long else self.rng.choice(ABSENCE_REASONS)
    return {"lecturerId": lec.id, "from": begin, "to": end, "reason": reason}

  def lecturerAvailabilityChanged(self, ts):
    lec = self.rng.choice([lec for lec in self.ds.lecturers if lec.availability])
    weekend = any(a.day in ("So", "Nd") for a in lec.availability)
    weekday = any(a.day not in ("So", "Nd") for a in lec.availability)
    if lec.competences[0] in self.external:
      new = (self.avail.external("full-time") if weekday else []) + (self.avail.external("part-time") if weekend else [])
    else:
      new = (self.avail.weekday() if weekday else []) + (self.avail.weekend() if weekend else [])
    return {"lecturerId": lec.id, "effectiveFrom": self.effective(ts, (7, 14)), "availability": [a.model_dump(mode="json") for a in new]}

  def groupSizeChanged(self, ts):
    g = self.rng.choice([g for g in self.ds.groups if g.kind != "cohort"])
    delta = self.rng.randint(*self.spec.size_change) * self.rng.choice([-1, 1])
    self.sizes[g.id] = max(1, self.sizes[g.id] + delta)
    return {"groupId": g.id, "size": self.sizes[g.id], "effectiveFrom": self.effective(ts)}

  def _new_id(self, make) -> str:
    """Pierwsze wolne id z make(1), make(2), ... — nowe encje nie kolidują z danymi ani ze sobą."""
    n = 1
    while make(n) in self.taken_ids:
      n += 1
    self.taken_ids.add(make(n))
    return make(n)

  def _mode(self, g: Group) -> str:
    return next(p.study_mode for p in self.ds.programs if p.id == g.program_id)

  def _cohort(self, g: Group) -> Group:
    while g.parent_id:
      g = self.groups[g.parent_id]
    return g

  def groupAdded(self, ts):
    ex = self.rng.choice([g for g in self.ds.groups if g.kind == "exercise"])
    cohort = self._cohort(ex)
    labs = [g for g in self.ds.groups if g.kind == "lab" and self._cohort(g).id == cohort.id]
    gid = self._new_id(lambda n: f"{cohort.id}-L{n}")
    group = Group(id=gid, program_id=ex.program_id, parent_id=ex.id, kind="lab", size=self.rng.randint(5, self.preset.groups.lab))
    sibling = next((g for g in labs if g.parent_id == ex.id), None)
    classes = [c.model_copy(update={"id": self._new_id(lambda n, c=c: f"{c.subject_id}-{c.form}-{gid}" + (f"-{n}" if n > 1 else "")),
                                    "group_id": gid, "lecturer_id": None})
               for c in self.ds.classes if sibling and c.group_id == sibling.id]
    return {"group": group.model_dump(mode="json"), "classes": [c.model_dump(mode="json") for c in classes]}

  def subjectAdded(self, ts):
    cohort = self.rng.choice([g for g in self.ds.groups if g.kind == "cohort"])
    weekend = self._mode(cohort) == "part-time"
    # tylko kompetencje, które ma ktoś dostępny w dniach tego trybu studiów
    have = {c for lec in self.ds.lecturers if any((a.day in ("So", "Nd")) == weekend for a in lec.availability)
            for c in lec.competences}
    options = [e for e in ELECTIVES if e[1] in have] or [("Przedmiot obieralny", sorted(have)[0])]
    name, comp = self.rng.choice(options)
    sid = self._new_id(lambda n: f"OBIER-{n}")
    members = [g for g in self.ds.groups if g.kind in ("lecture", "lab") and self._cohort(g).id == cohort.id]
    classes = [Class(id=f"{sid}-{'W' if g.kind == 'lecture' else 'L'}-{g.id}", subject_id=sid, form="W" if g.kind == "lecture" else "L",
                     group_id=g.id, units_per_week=2, units_per_meeting=2, parity="every",
                     room_type="lecture-hall" if g.kind == "lecture" else "lab-it") for g in members]
    return {"subject": Subject(id=sid, name=name, competence=comp).model_dump(mode="json"),
            "classes": [c.model_dump(mode="json") for c in classes]}

  def calendarExceptionAdded(self, ts):
    day = self.effective(ts, (3, 21))
    partial = self.rng.random() < 0.5
    exc = Rector(id=self._new_id(lambda n: f"zdarzenie-rektorskie-{n}"), type="rector", from_=day, to=day,
                 from_time="12:00" if partial else None, to_time="20:00" if partial else None,
                 label="Godziny rektorskie ogłoszone w trakcie semestru" if partial else "Dzień rektorski ogłoszony w trakcie semestru")
    return {"exception": exc.model_dump(mode="json")}


def _timestamps(spec: EventSpec, start: dt.date, end: dt.date, rng: random.Random) -> list[dt.datetime]:
  out = []
  t = dt.datetime.combine(start - dt.timedelta(days=spec.start_before_semester_days), dt.time())
  stop = dt.datetime.combine(end, dt.time())
  while True:
    t += dt.timedelta(days=rng.expovariate(spec.per_week / 7))
    if t >= stop:
      return sorted(out)
    minute = rng.randrange(7 * 60, 20 * 60, 5)  # informacja przychodzi w godzinach pracy
    out.append(dt.datetime.combine(t.date(), dt.time(minute // 60, minute % 60)))


def generate_events(ds: Dataset, preset: Preset, seed: int, *, errors: bool = True) -> EventStreamResult:
  spec = preset.events
  rng = random.Random(f"{seed}:events")
  builder = _Builder(ds, preset, rng)
  for name, given, known in [("events.weights", spec.weights, EVENT_TYPES), ("events.error_weights", spec.error_weights, ERROR_KINDS)]:
    if set(given) - set(known):
      raise ValueError(f"{name}: nieznane klucze {sorted(set(given) - set(known))}, dozwolone: {known}")
  types, weights = zip(*spec.weights.items())

  events = []
  for ts in _timestamps(spec, ds.calendar.semester_start, ds.calendar.semester_end, rng):
    kind = rng.choices(types, weights)[0]
    raw = {"id": f"E-{len(events) + 1:04d}", "timestamp": ts.isoformat(), "type": kind, "payload": getattr(builder, kind)(ts)}
    events.append(dump_event(parse_event(_jsonable(raw))))  # każde poprawne zdarzenie przechodzi schemat

  if errors:
    lines, manifest = inject_errors(events, spec, random.Random(f"{seed}:events-errors"))
  else:
    lines, manifest = [to_line(e) for e in events], []
  meta = {"seed": seed, "preset": preset.name, "events": len(events), "lines": len(lines), "errors": len(manifest),
          "types": dict(Counter(e["type"] for e in events))}
  return EventStreamResult(lines, manifest, meta)


def _jsonable(obj):
  if isinstance(obj, dict):
    return {k: _jsonable(v) for k, v in obj.items()}
  if isinstance(obj, list):
    return [_jsonable(v) for v in obj]
  if isinstance(obj, (dt.date, dt.datetime)):
    return obj.isoformat()
  return obj
