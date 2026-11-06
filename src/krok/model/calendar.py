"""Kalendarz semestru i wyjątki (`calendar.json`)."""

import datetime as dt
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import Day, Id, Model, Time, WeekParity


class Scope(Model):
  faculty_ids: list[Id] | None = None
  group_ids: list[Id] | None = None
  room_ids: list[Id] | None = None
  lecturer_ids: list[Id] | None = None


class _RangeException(Model):
  id: Id
  from_: dt.date = Field(alias="from")
  to: dt.date
  from_time: Time | None = None
  to_time: Time | None = None
  scope: Scope | None = None
  label: str = ""

  @model_validator(mode="after")
  def _check(self):
    if self.from_ > self.to:
      raise ValueError(f"{self.id}: from po to")
    if (self.from_time is None) != (self.to_time is None):
      raise ValueError(f"{self.id}: fromTime i toTime podaje się razem")
    return self


class Holiday(_RangeException):
  type: Literal["holiday"]


class Rector(_RangeException):
  type: Literal["rector"]


class ResourceBlock(_RangeException):
  type: Literal["resourceBlock"]
  scope: Scope


class DayOverride(Model):
  id: Id
  type: Literal["dayOverride"]
  date: dt.date
  follows_day: Day
  follows_parity: WeekParity | None = None
  scope: Scope | None = None
  label: str = ""


CalendarException = Annotated[Holiday | Rector | ResourceBlock | DayOverride, Field(discriminator="type")]


class PartTimeSessions(Model):
  first_weekend: dt.date
  every_weeks: int = Field(ge=1)


class WeekParityOverride(Model):
  week_of: dt.date
  parity: WeekParity | None


class Calendar(Model):
  semester_start: dt.date
  semester_end: dt.date
  first_week_parity: WeekParity
  part_time_sessions: PartTimeSessions | None = None
  week_parity_overrides: list[WeekParityOverride] = []
  exceptions: list[CalendarException] = []

  @model_validator(mode="after")
  def _check(self):
    if self.semester_start >= self.semester_end:
      raise ValueError("semesterStart musi być przed semesterEnd")
    return self
