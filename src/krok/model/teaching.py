"""Przedmioty i zajęcia do zaplanowania."""

from typing import Annotated

from pydantic import Field, model_validator

from .base import Form, Id, Model, Parity


class Subject(Model):
  id: Id
  name: str
  competence: Id
  # Obsada per forma. Brak pola lub klucza = wszyscy prowadzący z kompetencją `competence`.
  lecturer_pools: dict[Form, Annotated[list[Id], Field(min_length=1)]] | None = None

  def pool(self, form: str) -> list[str] | None:
    return (self.lecturer_pools or {}).get(form)


class Class(Model):
  """Jedno wymaganie = jedna forma przedmiotu dla jednej grupy."""

  id: Id
  subject_id: Id
  form: Form
  group_id: Id
  units_per_week: int = Field(ge=1)
  units_per_meeting: int = Field(ge=1)
  parity: Parity
  room_type: Id
  lecturer_id: Id | None = None

  @model_validator(mode="after")
  def _check(self):
    if self.units_per_week % self.units_per_meeting:
      raise ValueError(f"{self.id}: unitsPerWeek musi być podzielne przez unitsPerMeeting")
    return self

  @property
  def meetings_per_week(self) -> int:
    return self.units_per_week // self.units_per_meeting
