"""Obsada przedmiotów: `lecturerPools` per forma (docs/etap1/model-domeny.md, „Przedmiot”).

Część przedmiotów (`lecturers.pool_share`) dostaje pule. Pula formy:
- zawiera tylko prowadzących z kompetencją przedmiotu i niepustą dostępnością,
- ma co najmniej jedną osobę dostępną w dni tygodnia i jedną w weekend, jeśli forma ma zajęcia dzienne/zaoczne,
- ma łączny limit ≥ zapotrzebowanie formy × `slack` i łączną dostępność w każdym trybie ≥ czas zajęć × `slack`,
  o ile kandydatów starcza.
Gdy tych warunków nie da się spełnić, forma zostaje bez puli (solver wybiera spośród wszystkich z kompetencją).
"""

import random
from collections import defaultdict
from functools import cache

from krok.model import Class, Config, Group, Lecturer, Program, Subject
from krok.perception.measures import meeting_minutes

from .lecturers import available_minutes, class_load, class_modes
from .settings import Preset

WEEKEND = ("So", "Nd")


def _fits(lec: Lecturer, mode: str) -> bool:
  return any((a.day in WEEKEND) == (mode == "part-time") for a in lec.availability)


def build_pools(preset: Preset, config: Config, subjects: list[Subject], classes: list[Class], lecturers: list[Lecturer],
                groups: list[Group], programs: list[Program], rng: random.Random) -> None:
  spec = preset.lecturers
  modes = class_modes(classes, groups, programs)
  demand: dict[tuple[str, str], float] = defaultdict(float)
  form_modes: dict[tuple[str, str], set[str]] = defaultdict(set)
  by_form: dict[tuple[str, str], list[Class]] = defaultdict(list)
  for c in classes:
    demand[(c.subject_id, c.form)] += class_load(c)
    form_modes[(c.subject_id, c.form)].add(modes[c.id])
    by_form[(c.subject_id, c.form)].append(c)

  @cache
  def minutes(key: tuple[str, str], mode: str) -> int:
    cs = [c for c in by_form[key] if modes[c.id] == mode]
    return max(sum(c.meetings_per_week * meeting_minutes(c.units_per_meeting, config.time_grid)
                   for c in cs if c.parity in ("every", w)) for w in ("A", "B"))

  def enough(pool: list[Lecturer], key: tuple[str, str]) -> bool:
    if sum(lec.max_units_weekly for lec in pool) < demand[key] * spec.slack:
      return False
    return all(sum(available_minutes(lec, m, config) for lec in pool) >= minutes(key, m) * spec.slack for m in form_modes[key])

  for s in subjects:
    if rng.random() >= spec.pool_share:
      continue
    candidates = [lec for lec in lecturers if s.competence in lec.competences and lec.availability]
    pools = {}
    for form in ("W", "C", "L", "P"):
      key = (s.id, form)
      if key not in demand:
        continue
      pool: list[Lecturer] = []
      for mode in sorted(form_modes[key]):
        fitting = [lec for lec in candidates if _fits(lec, mode) and lec not in pool]
        if not fitting:
          break
        pool.append(rng.choice(fitting))
      else:
        rest = [lec for lec in candidates if lec not in pool]
        rng.shuffle(rest)
        while rest and not enough(pool, key):
          pool.append(rest.pop())
        if enough(pool, key):
          pools[form] = sorted(lec.id for lec in pool)
    s.lecturer_pools = pools or None
