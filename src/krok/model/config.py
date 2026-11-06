"""Zasady układania: siatka czasu, przerwy, reguły (`config.json`)."""

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Day, Id, Model, StudyMode, Time, to_minutes


class BreakOverride(Model):
  from_: Time = Field(alias="from")
  to: Time
  inner_minutes: int | None = Field(default=None, ge=0)
  min_between_classes_minutes: int | None = Field(default=None, ge=0)


class Breaks(Model):
  inner_minutes: int = Field(ge=0)
  min_between_classes_minutes: int = Field(ge=0)
  overrides: list[BreakOverride] = []


class TimeGrid(Model):
  days: list[Day]
  days_by_study_mode: dict[StudyMode, list[Day]]
  day_start: Time
  day_end: Time
  unit_minutes: int = Field(gt=0)
  start_step_minutes: int = Field(gt=0)
  allowed_start_times: list[Time] | None = None
  breaks: Breaks

  @model_validator(mode="after")
  def _check(self):
    if to_minutes(self.day_start) >= to_minutes(self.day_end):
      raise ValueError("dayStart musi być przed dayEnd")
    return self


class Target(Model):
  """Warunek wartości docelowej reguły, np. {"stat": "meanPerLeafGroup", "max": 120} (docs/etap1/kryteria.md)."""

  stat: Id
  max: float


class Rule(Model):
  id: Id
  enabled: bool = True
  kind: Literal["hard", "soft"]
  weight: float | None = None
  params: dict[str, Any] = {}
  target: list[Target] | None = None


class Config(Model):
  time_grid: TimeGrid
  room_types: list[Id]
  walking_speed_kmh: float = Field(gt=0)
  route_factor: float = Field(ge=1)  # trasa piesza / linia prosta
  rules: list[Rule] = []
