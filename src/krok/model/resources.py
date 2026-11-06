"""Zasoby: budynki, sale, prowadzący."""

from pydantic import Field, model_validator

from .base import Day, Id, Model, Time, to_minutes


class Building(Model):
  id: Id
  name: str
  lat: float = Field(ge=-90, le=90)
  lon: float = Field(ge=-180, le=180)
  faculty_ids: list[Id]


class Room(Model):
  id: Id
  building_id: Id
  number: str
  type: Id
  capacity: int = Field(ge=1)
  faculty_ids: list[Id]

  @model_validator(mode="after")
  def _check(self):
    if self.id != f"{self.building_id}/{self.number}":
      raise ValueError(f"{self.id}: id sali musi być równe buildingId/number")
    return self


class Availability(Model):
  day: Day
  from_: Time = Field(alias="from")
  to: Time

  @model_validator(mode="after")
  def _check(self):
    if to_minutes(self.from_) >= to_minutes(self.to):
      raise ValueError(f"{self.day} {self.from_}-{self.to}: from musi być przed to")
    return self


class Lecturer(Model):
  id: Id
  name: str
  faculty_ids: list[Id]
  competences: list[Id]
  home_building_id: Id
  availability: list[Availability]
  max_units_weekly: int = Field(ge=0)
