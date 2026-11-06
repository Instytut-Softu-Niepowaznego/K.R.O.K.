"""Wspólne typy i klasa bazowa modelu domeny (docs/etap1/model-domeny.md)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel


class Model(BaseModel):
  """Pola w Pythonie w snake_case, w JSON w camelCase. Nieznane pola to błąd."""

  model_config = ConfigDict(
    alias_generator=to_camel,
    validate_by_name=True,
    validate_by_alias=True,
    serialize_by_alias=True,
    extra="forbid",
  )


Id = Annotated[str, StringConstraints(min_length=1)]
Time = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

Day = Literal["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]
DAYS: tuple[Day, ...] = ("Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd")

StudyMode = Literal["full-time", "part-time"]
Degree = Literal["inz", "lic", "mgr"]
Parity = Literal["every", "A", "B"]
WeekParity = Literal["A", "B"]
Form = Literal["W", "C", "L", "P"]
GroupKind = Literal["cohort", "lecture", "exercise", "lab", "project"]


def to_minutes(t: str) -> int:
  h, m = t.split(":")
  return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
  return f"{minutes // 60:02d}:{minutes % 60:02d}"
