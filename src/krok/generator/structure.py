"""Wydział, kierunki, programy i drzewo grup."""

import math
import random
from dataclasses import dataclass, field

from krok.model import Faculty, Group, Program, StudyField

from .settings import Preset, ProgramSpec


@dataclass
class Cohort:
  program: Program
  spec: ProgramSpec
  field_id: str
  semester: int
  groups: dict[str, list[Group]] = field(default_factory=dict)  # kind -> grupy


def split(total: int, parts: int) -> list[int]:
  return [total // parts + (1 if i < total % parts else 0) for i in range(parts)]


def program_id(faculty_id: str, field_id: str, spec: ProgramSpec) -> str:
  suffix = "-z" if spec.study_mode == "part-time" else ""
  return f"{faculty_id}-{field_id}-{spec.degree}{suffix}"


def build_structure(preset: Preset, rng: random.Random):
  faculties = [Faculty(id=preset.faculty_id, name=preset.faculty_name)]
  fields, programs, groups, cohorts = [], [], [], []
  sizes = preset.groups

  for f in preset.fields:
    fid = f"{preset.faculty_id}-{f.id}"
    fields.append(StudyField(id=fid, faculty_id=preset.faculty_id, name=f.name))
    for spec in f.programs:
      prog = Program(id=program_id(preset.faculty_id, f.id, spec), field_id=fid, degree=spec.degree,
                     study_mode=spec.study_mode, semesters=spec.semesters)
      programs.append(prog)
      first_size = rng.randint(*spec.cohort_size)
      for sem in spec.active():
        if not 1 <= sem <= spec.semesters:
          raise ValueError(f"{prog.id}: aktywny semestr {sem} poza zakresem 1..{spec.semesters}")
        size = max(1, round(first_size * (1 - spec.attrition_per_year) ** ((sem - 1) // 2)))
        cohort = Cohort(prog, spec, f.id, sem)
        cid = f"{prog.id}-s{sem}"
        root = Group(id=cid, program_id=prog.id, kind="cohort", semester=sem, size=size)
        lectures, exercises, labs = [], [], []
        for lsize in split(size, math.ceil(size / sizes.lecture_max)):
          lec = Group(id=f"{cid}-W{len(lectures) + 1}", program_id=prog.id, parent_id=cid, kind="lecture", size=lsize)
          lectures.append(lec)
          for esize in split(lsize, math.ceil(lsize / sizes.exercise)):
            ex = Group(id=f"{cid}-C{len(exercises) + 1}", program_id=prog.id, parent_id=lec.id, kind="exercise", size=esize)
            exercises.append(ex)
            for labsize in split(esize, math.ceil(esize / sizes.lab)):
              labs.append(Group(id=f"{cid}-L{len(labs) + 1}", program_id=prog.id, parent_id=ex.id, kind="lab", size=labsize))
        cohort.groups = {"cohort": [root], "lecture": lectures, "exercise": exercises, "lab": labs}
        groups += [root, *lectures, *exercises, *labs]
        cohorts.append(cohort)

  return faculties, fields, programs, groups, cohorts
