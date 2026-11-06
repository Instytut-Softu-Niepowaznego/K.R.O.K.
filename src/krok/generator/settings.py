"""Konfiguracja generatora (presety TOML) i katalog przedmiotów."""

import datetime as dt
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from krok.model import Degree, Form, StudyMode

PRESETS_DIR = Path(__file__).parent / "presets"
BASE_DATA_DIR = Path(__file__).parent.parent / "data"

IntRange = tuple[int, int]
TimeRange = tuple[str, str]


class Strict(BaseModel):
  model_config = ConfigDict(extra="forbid")


# --- katalog ---

class CatalogCompetence(Strict):
  id: str
  external: bool = False
  home_buildings: list[str] = []


class CatalogSubject(Strict):
  id: str
  name: str
  competence: str
  level: Literal["inz", "lic", "mgr", "any"]
  semesters: IntRange
  forms: dict[Form, int]
  lab_type: str = "lab-it"
  fields: list[str] = ["*"]

  def fits(self, field_id: str, degree: str, semester: int) -> bool:
    level_ok = self.level == "any" or self.level == degree or (self.level == "inz" and degree == "lic")
    field_ok = "*" in self.fields or field_id in self.fields
    return level_ok and field_ok and self.semesters[0] <= semester <= self.semesters[1]


class Catalog(Strict):
  competence: list[CatalogCompetence]
  subject: list[CatalogSubject]

  @model_validator(mode="after")
  def _check(self):
    known = {c.id for c in self.competence}
    missing = sorted({s.competence for s in self.subject} - known)
    if missing:
      raise ValueError(f"katalog: przedmioty z nieznaną kompetencją {missing}")
    return self


# --- preset ---

class ProgramSpec(Strict):
  degree: Degree
  study_mode: StudyMode
  semesters: int = Field(ge=1)
  active_semesters: list[int] | None = None  # None = nieparzyste (semestr zimowy)
  cohort_size: IntRange  # rozmiar rocznika na 1. semestrze
  attrition_per_year: float = Field(default=0.12, ge=0, lt=1)
  subjects_per_semester: IntRange = (5, 7)

  def active(self) -> list[int]:
    return self.active_semesters or list(range(1, self.semesters + 1, 2))


class FieldSpec(Strict):
  id: str
  name: str
  programs: list[ProgramSpec]


class GroupSizes(Strict):
  lecture_max: int = 120
  exercise: int = 30
  lab: int = 15


class AvailabilityPattern(Strict):
  weight: float = 1.0
  days: IntRange
  start: TimeRange  # losowy początek dnia pracy z przedziału
  end: TimeRange


class LecturerSpec(Strict):
  max_units_weekly: IntRange = (12, 20)
  slack: float = Field(default=1.3, ge=1)  # pojemność prowadzących / zapotrzebowanie
  extra_competences: IntRange = (0, 2)
  cross_mode_share: float = Field(default=0.5, ge=0, le=1)  # część zaocznych prowadzonych przez kadrę dzienną
  pool_share: float = Field(default=0.6, ge=0, le=1)  # część przedmiotów z obsadą (lecturerPools)
  time_step_minutes: int = 15
  external_days: IntRange = (1, 2)
  external_hours: IntRange = (4, 7)
  weekend: AvailabilityPattern = AvailabilityPattern(days=(2, 2), start=("07:30", "09:00"), end=("16:00", "20:00"))
  patterns: dict[str, AvailabilityPattern] = {
    "pelny": AvailabilityPattern(weight=4, days=(4, 5), start=("07:30", "09:00"), end=("15:00", "20:00")),
    "rano": AvailabilityPattern(weight=2, days=(3, 5), start=("07:30", "08:30"), end=("13:00", "15:00")),
    "popoludnie": AvailabilityPattern(weight=2, days=(3, 5), start=("11:00", "13:00"), end=("18:00", "20:00")),
    "czesciowy": AvailabilityPattern(weight=1, days=(2, 3), start=("08:00", "10:00"), end=("16:00", "19:00")),
  }


class CalendarSpec(Strict):
  semester_start: dt.date = dt.date(2026, 10, 1)
  semester_end: dt.date = dt.date(2027, 1, 29)
  first_week_parity: Literal["A", "B"] = "A"
  part_time_every_weeks: int = 2
  breaks: list[tuple[dt.date, dt.date, str]] = [(dt.date(2026, 12, 22), dt.date(2027, 1, 6), "Przerwa świąteczna")]
  rector_days: int = 1
  rector_hours: int = 1
  room_blocks: int = 1
  day_override: bool = True


class EdgeCaseSpec(Strict):
  enabled: bool = False


class NoiseSpec(Strict):
  enabled: bool = False
  missing_field_rate: float = 0.02
  key_typo_rate: float = 0.01
  bad_reference_rate: float = 0.01
  outlier_rate: float = 0.01
  duplicate_rate: float = 0.01


class Preset(Strict):
  name: str
  description: str = ""
  seed: int = 42
  faculty_id: str = "WJ"
  faculty_name: str = "Wydział Informatyki"
  base_data: str = "wj"
  catalog: str = "katalog-wj.toml"
  fields: list[FieldSpec]
  groups: GroupSizes = GroupSizes()
  part_time_units_factor: float = Field(default=0.6, gt=0, le=1)
  ab_share: float = Field(default=0.15, ge=0, le=1)  # część laboratoriów co 2 tygodnie (A/B)
  room_capacity: dict[str, IntRange] = {  # gdy w rooms.json brakuje capacity
    "lecture-hall": (90, 200), "classroom": (24, 40), "lab-it": (15, 16), "lab-electronics": (12, 16),
  }
  lecturers: LecturerSpec = LecturerSpec()
  calendar: CalendarSpec = CalendarSpec()
  edge_cases: EdgeCaseSpec = EdgeCaseSpec()
  noise: NoiseSpec = NoiseSpec()


def _merge(base: dict, over: dict) -> dict:
  out = dict(base)
  for k, v in over.items():
    out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
  return out


def _read_preset(ref: str) -> dict:
  path = Path(ref) if ref.endswith(".toml") else PRESETS_DIR / f"{ref}.toml"
  if not path.exists():
    known = sorted(p.stem for p in PRESETS_DIR.glob("*.toml") if not p.stem.startswith("katalog"))
    raise FileNotFoundError(f"Nie ma presetu {ref}. Dostępne: {', '.join(known)}")
  data = tomllib.loads(path.read_text(encoding="utf-8"))
  parent = data.pop("extends", None)
  return _merge(_read_preset(parent), data) if parent else data


def load_preset(ref: str) -> Preset:
  """Nazwa presetu z presets/ albo ścieżka do pliku .toml. `extends = "mini"` dziedziczy i nadpisuje pola."""
  return Preset.model_validate(_read_preset(ref))


def load_catalog(name: str) -> Catalog:
  path = Path(name) if Path(name).exists() else PRESETS_DIR / name
  return Catalog.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def list_presets() -> list[str]:
  return sorted(p.stem for p in PRESETS_DIR.glob("*.toml") if not p.stem.startswith("katalog"))
