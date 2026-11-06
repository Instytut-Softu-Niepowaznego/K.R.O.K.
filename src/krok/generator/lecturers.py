"""Prowadzący: kompetencje, budynek macierzysty, dostępność w godzinach, limit jednostek.

Liczba prowadzących wynika z zapotrzebowania: dla każdej kompetencji i trybu studiów
suma jednostek zajęć × `slack` / średni limit tygodniowy.
"""

import math
import random
from collections import defaultdict

from krok.model import Availability, Building, Class, Config, Group, Lecturer, Program, Subject, to_hhmm, to_minutes
from krok.perception.measures import meeting_minutes

from .names import NameGenerator
from .settings import AvailabilityPattern, Catalog, Preset


def class_load(c: Class) -> float:
  """Średnie obciążenie tygodniowe: zajęcia A/B odbywają się co drugi tydzień."""
  return c.units_per_week * (1 if c.parity == "every" else 0.5)


def peak_units(classes: list[Class]) -> int:
  """Jednostki w gorszym tygodniu (A: every + A, B: every + B). Tak limit prowadzącego sprawdza reguła
  limit-godzin-prowadzacego, więc limity dobieramy pod ten tydzień, a nie pod średnią."""
  return max(sum(c.units_per_week for c in classes if c.parity in ("every", w)) for w in ("A", "B"))


def available_minutes(lec: Lecturer, mode: str, config: Config) -> int:
  """Łączna dostępność prowadzącego w dniach danego trybu studiów (minuty w tygodniu)."""
  days = config.time_grid.days_by_study_mode[mode]
  return sum(to_minutes(a.to) - to_minutes(a.from_) for a in lec.availability if a.day in days)


def class_modes(classes: list[Class], groups: list[Group], programs: list[Program]) -> dict[str, str]:
  mode = {p.id: p.study_mode for p in programs}
  group_mode = {g.id: mode[g.program_id] for g in groups}
  return {c.id: group_mode[c.group_id] for c in classes}


class AvailabilityGenerator:
  def __init__(self, preset: Preset, config: Config, rng: random.Random):
    self.spec = preset.lecturers
    self.grid = config.time_grid
    self.rng = rng
    self.day_start = to_minutes(self.grid.day_start)
    self.day_end = to_minutes(self.grid.day_end)
    names, patterns = zip(*self.spec.patterns.items())
    self.pattern_names, self.pattern_weights, self.patterns = names, [p.weight for p in patterns], dict(zip(names, patterns))

  def _time(self, lo: str, hi: str) -> int:
    step = self.spec.time_step_minutes
    a, b = to_minutes(lo), to_minutes(hi)
    return min(max(self.rng.randrange(a, b + 1, step) if b > a else a, self.day_start), self.day_end)

  def _days(self, mode: str, count: tuple[int, int]) -> list[str]:
    days = self.grid.days_by_study_mode[mode]
    n = min(len(days), self.rng.randint(*count))
    picked = set(self.rng.sample(days, n))
    return [d for d in days if d in picked]

  def from_pattern(self, pattern: AvailabilityPattern, mode: str) -> list[Availability]:
    out = []
    for day in self._days(mode, pattern.days):
      start = self._time(*pattern.start)
      end = max(self._time(*pattern.end), start + 120)
      out.append(Availability(day=day, from_=to_hhmm(start), to=to_hhmm(min(end, self.day_end))))
    return out

  def weekday(self) -> list[Availability]:
    name = self.rng.choices(self.pattern_names, self.pattern_weights)[0]
    return self.from_pattern(self.patterns[name], "full-time")

  def weekend(self) -> list[Availability]:
    return self.from_pattern(self.spec.weekend, "part-time")

  def external(self, mode: str) -> list[Availability]:
    out = []
    step = self.spec.time_step_minutes
    for day in self._days(mode, self.spec.external_days):
      length = self.rng.randint(*self.spec.external_hours) * 60
      latest = max(self.day_start, self.day_end - length)
      start = self.day_start + self.rng.randrange(0, latest - self.day_start + 1, step)
      out.append(Availability(day=day, from_=to_hhmm(start), to=to_hhmm(min(start + length, self.day_end))))
    return out


def build_lecturers(preset: Preset, catalog: Catalog, config: Config, buildings: list[Building], subjects: list[Subject],
                    classes: list[Class], groups: list[Group], programs: list[Program], rng: random.Random) -> list[Lecturer]:
  spec = preset.lecturers
  comps = {c.id: c for c in catalog.competence}
  building_ids = [b.id for b in buildings]
  subject_comp = {s.id: s.competence for s in subjects}
  modes = class_modes(classes, groups, programs)
  avail = AvailabilityGenerator(preset, config, rng)
  names = NameGenerator(rng)
  avg_limit = sum(spec.max_units_weekly) / 2

  by_key: dict[tuple[str, str], list[Class]] = defaultdict(list)
  for c in classes:
    by_key[(subject_comp[c.subject_id], modes[c.id])].append(c)
  demand: dict[tuple[str, str], int] = defaultdict(int, {key: peak_units(cs) for key, cs in by_key.items()})
  # Prowadzący nie mają dwóch zajęć naraz, więc ich łączna dostępność musi pokryć czas zajęć w gorszym tygodniu
  # (A lub B). Bez tego prowadzący spoza wydziału (1–2 dni) nie wyrabiali się z matematyką i fizyką.
  minutes_need: dict[tuple[str, str], int] = {}
  for key, cs in by_key.items():
    minutes_need[key] = max(sum(c.meetings_per_week * meeting_minutes(c.units_per_meeting, config.time_grid)
                                for c in cs if c.parity in ("every", w)) for w in ("A", "B"))

  lecturers: list[Lecturer] = []
  by_comp: dict[str, list[Lecturer]] = defaultdict(list)

  def new(comp: str, mode: str) -> Lecturer:
    external = comps[comp].external
    homes = [b for b in comps[comp].home_buildings if b in building_ids] or building_ids
    availability = avail.external(mode) if external else (avail.weekday() if mode == "full-time" else avail.weekend())
    lec = Lecturer(id=f"P-{len(lecturers) + 1:04d}", name=names(), faculty_ids=[preset.faculty_id], competences=[comp],
                   home_building_id=rng.choice(homes), availability=availability,
                   max_units_weekly=rng.randint(*spec.max_units_weekly))
    lecturers.append(lec)
    by_comp[comp].append(lec)
    return lec

  def top_up(comp: str, mode: str) -> None:
    need = minutes_need.get((comp, mode), 0) * spec.slack
    have = sum(available_minutes(lec, mode, config) for lec in by_comp[comp])
    while have < need:
      added = available_minutes(new(comp, mode), mode, config)
      if added <= 0:  # np. brak dni dla trybu w konfiguracji: kolejni prowadzący nic nie zmienią
        break
      have += added

  used_comps = [c.id for c in catalog.competence if any(k[0] == c.id for k in demand)]
  for comp in used_comps:
    for _ in range(math.ceil(demand[(comp, "full-time")] * spec.slack / avg_limit)):
      new(comp, "full-time")
    top_up(comp, "full-time")
    need = math.ceil(demand[(comp, "part-time")] * spec.slack / avg_limit)
    if not need:
      continue
    weekday_staff = [lec for lec in by_comp[comp] if not any(a.day in ("So", "Nd") for a in lec.availability)]
    shared = rng.sample(weekday_staff, min(len(weekday_staff), round(need * spec.cross_mode_share)))
    for lec in shared:
      extra = avail.external("part-time") if comps[comp].external else avail.weekend()
      lec.availability = lec.availability + extra
    for _ in range(need - len(shared)):
      new(comp, "part-time")
    top_up(comp, "part-time")

  internal = [c for c in used_comps if not comps[c].external]
  for lec in lecturers:
    if comps[lec.competences[0]].external:
      continue
    others = [c for c in internal if c not in lec.competences]
    k = min(len(others), rng.randint(*spec.extra_competences))
    lec.competences = lec.competences + sorted(rng.sample(others, k), key=internal.index)
  return lecturers
