"""Struktura studentów: wydziały, kierunki, programy, drzewo grup."""

from pydantic import Field, model_validator

from .base import Degree, GroupKind, Id, Model, StudyMode


class Faculty(Model):
  id: Id
  name: str


class StudyField(Model):
  """Kierunek studiów (w JSON: `fields`)."""

  id: Id
  faculty_id: Id
  name: str


class Program(Model):
  id: Id
  field_id: Id
  degree: Degree
  study_mode: StudyMode
  semesters: int = Field(ge=1)


class Group(Model):
  id: Id
  program_id: Id
  parent_id: Id | None = None
  kind: GroupKind
  semester: int | None = Field(default=None, ge=1)
  size: int = Field(ge=1)

  @model_validator(mode="after")
  def _check(self):
    if self.kind == "cohort" and (self.parent_id is not None or self.semester is None):
      raise ValueError(f"{self.id}: rocznik (cohort) nie ma rodzica i ma semester")
    if self.kind != "cohort" and self.parent_id is None:
      raise ValueError(f"{self.id}: tylko rocznik może nie mieć rodzica")
    return self
