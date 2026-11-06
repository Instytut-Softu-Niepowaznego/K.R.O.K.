"""Zdarzenia z otoczenia (#8): jedna linia JSONL = {id, timestamp, type, payload}.

`timestamp` to chwila, w której system dowiaduje się o zmianie (czas lokalny, bez strefy).
Daty w `payload` mówią, kiedy zmiana obowiązuje. O skutku (terminarz vs ponowne planowanie)
decyduje konsument (#9, #17), zgodnie z docs/etap1/problem-i-otoczenie.md.
"""

import datetime as dt
import json
from typing import Annotated, Literal

from pydantic import Field, NaiveDatetime, TypeAdapter, model_validator

from .base import Id, Model, Time
from .calendar import CalendarException
from .resources import Availability
from .structure import Group
from .teaching import Class, Subject


def _check_period(p):
  """Wspólna walidacja okresu: from ≤ to, fromTime i toTime razem."""
  if p.from_ > p.to:
    raise ValueError("from po to")
  if (getattr(p, "from_time", None) is None) != (getattr(p, "to_time", None) is None):
    raise ValueError("fromTime i toTime podaje się razem")
  return p


class RoomUnavailablePayload(Model):
  """Sala niedostępna od `from` do `to`, opcjonalnie tylko w godzinach `fromTime`–`toTime`."""
  room_id: Id
  from_: dt.date = Field(alias="from")
  to: dt.date
  from_time: Time | None = None
  to_time: Time | None = None
  reason: str = ""

  _check = model_validator(mode="after")(_check_period)


class LecturerAbsentPayload(Model):
  """Nieobecność prowadzącego od `from` do `to` (choroba, wyjazd, urlop)."""
  lecturer_id: Id
  from_: dt.date = Field(alias="from")
  to: dt.date
  reason: str = ""

  _check = model_validator(mode="after")(_check_period)


class LecturerAvailabilityChangedPayload(Model):
  """Nowa, pełna lista przedziałów dostępności prowadzącego, obowiązująca od `effectiveFrom`."""
  lecturer_id: Id
  effective_from: dt.date
  availability: list[Availability]


class GroupSizeChangedPayload(Model):
  """Nowa liczebność jednej grupy od `effectiveFrom`; przodkowie nie są przeliczani."""
  group_id: Id
  size: int = Field(ge=1)
  effective_from: dt.date


class GroupAddedPayload(Model):
  """Nowa grupa (encja `Group`) razem z jej zajęciami; lista zajęć może być pusta."""
  group: Group
  classes: list[Class]  # może być pusta, ale pole jest wymagane


class SubjectAddedPayload(Model):
  """Nowy przedmiot (encja `Subject`) z co najmniej jednymi zajęciami."""
  subject: Subject
  classes: list[Class] = Field(min_length=1)


class CalendarExceptionAddedPayload(Model):
  """Nowy wyjątek kalendarza, np. dzień rektorski ogłoszony w trakcie semestru."""
  exception: CalendarException


class _Event(Model):
  """Wspólne pola zdarzenia: `id` i `timestamp` (chwila, w której system dowiaduje się o zmianie)."""
  id: Id
  timestamp: NaiveDatetime  # czas lokalny bez strefy; offset (np. +02:00) to błąd schematu


class RoomUnavailable(_Event):
  """Zdarzenie `roomUnavailable`."""
  type: Literal["roomUnavailable"]
  payload: RoomUnavailablePayload


class LecturerAbsent(_Event):
  """Zdarzenie `lecturerAbsent`."""
  type: Literal["lecturerAbsent"]
  payload: LecturerAbsentPayload


class LecturerAvailabilityChanged(_Event):
  """Zdarzenie `lecturerAvailabilityChanged`."""
  type: Literal["lecturerAvailabilityChanged"]
  payload: LecturerAvailabilityChangedPayload


class GroupSizeChanged(_Event):
  """Zdarzenie `groupSizeChanged`."""
  type: Literal["groupSizeChanged"]
  payload: GroupSizeChangedPayload


class GroupAdded(_Event):
  """Zdarzenie `groupAdded`."""
  type: Literal["groupAdded"]
  payload: GroupAddedPayload


class SubjectAdded(_Event):
  """Zdarzenie `subjectAdded`."""
  type: Literal["subjectAdded"]
  payload: SubjectAddedPayload


class CalendarExceptionAdded(_Event):
  """Zdarzenie `calendarExceptionAdded`."""
  type: Literal["calendarExceptionAdded"]
  payload: CalendarExceptionAddedPayload


Event = Annotated[
  RoomUnavailable | LecturerAbsent | LecturerAvailabilityChanged | GroupSizeChanged | GroupAdded | SubjectAdded
  | CalendarExceptionAdded,
  Field(discriminator="type"),
]
EVENT_TYPES = ["roomUnavailable", "lecturerAbsent", "lecturerAvailabilityChanged", "groupSizeChanged", "groupAdded",
               "subjectAdded", "calendarExceptionAdded"]

_adapter = TypeAdapter(Event)


def parse_event(raw: dict | str) -> Event:
  """Waliduje jedno zdarzenie (słownik albo linia JSONL). Błędny schemat → pydantic.ValidationError."""
  return _adapter.validate_python(json.loads(raw) if isinstance(raw, str) else raw)


def dump_event(event: Event) -> dict:
  """Zdarzenie jako słownik gotowy do zapisu w JSONL (klucze camelCase, daty jako tekst)."""
  return _adapter.dump_python(event, mode="json")
