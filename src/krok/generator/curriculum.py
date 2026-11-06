"""Programy studiów: przedmioty na rocznik i zajęcia do zaplanowania."""

import random

from krok.model import Class, Subject

from .settings import Catalog, CatalogSubject, Preset
from .structure import Cohort

FORM_GROUP_KIND = {"W": "lecture", "C": "exercise", "L": "lab", "P": "lab"}  # projekt w sali lab -> grupy lab


def room_type(subject: CatalogSubject, form: str) -> str:
  return {"W": "lecture-hall", "C": "classroom"}.get(form, subject.lab_type)


def units_per_meeting(units: int) -> int:
  """2-jednostkowe bloki tam, gdzie się da; nieparzyste do 3 jednostek jako jeden blok."""
  if units % 2 == 0:
    return 2
  return units if units <= 3 else 1


def build_curriculum(preset: Preset, catalog: Catalog, cohorts: list[Cohort], rng: random.Random):
  order = {s.id: i for i, s in enumerate(catalog.subject)}
  used_subjects: dict[str, CatalogSubject] = {}
  classes: list[Class] = []
  used_in_program: dict[str, set[str]] = {}

  for cohort in cohorts:
    prog = cohort.program
    taken = used_in_program.setdefault(prog.id, set())
    candidates = [s for s in catalog.subject if s.id not in taken and s.fits(cohort.field_id, prog.degree, cohort.semester)]
    k = min(len(candidates), rng.randint(*cohort.spec.subjects_per_semester))
    chosen = sorted(rng.sample(candidates, k), key=lambda s: order[s.id])
    taken.update(s.id for s in chosen)

    for subj in chosen:
      used_subjects[subj.id] = subj
      ab = rng.random() < preset.ab_share
      for form, units in subj.forms.items():
        if units <= 0:
          continue
        if prog.study_mode == "part-time":
          units = max(1, round(units * preset.part_time_units_factor))
        per_meeting = units_per_meeting(units)
        targets = cohort.groups[FORM_GROUP_KIND[form]]
        for i, group in enumerate(targets):
          parity = ("A" if i % 2 == 0 else "B") if ab and form == "L" else "every"
          classes.append(Class(
            id=f"{subj.id}-{form}-{group.id}", subject_id=subj.id, form=form, group_id=group.id,
            units_per_week=units, units_per_meeting=per_meeting, parity=parity,
            room_type=room_type(subj, form),
          ))

  subjects = [Subject(id=s.id, name=s.name, competence=s.competence)
              for s in sorted(used_subjects.values(), key=lambda s: order[s.id])]
  return subjects, classes
