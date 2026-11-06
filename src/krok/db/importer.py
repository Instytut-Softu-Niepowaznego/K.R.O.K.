"""Import katalogu z plikami JSON do bazy. Zaszumione dane nie przechodzą przez load_dataset, więc rekordy czyścimy osobno."""

import json
import re
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Engine

from krok.model import Calendar, Config, Dataset, DayOverride, reference_errors
from krok.model.dataset import LIST_FILES

from .clean import Issue, Thresholds, clean_records
from .schema import ENTITIES, write_dataset

FACULTY_LISTS = ["faculties", "fields", "programs"]
REFERENCE_ERROR = re.compile(r"^(\w+)/(.+?)(?:/lecturerPools/\w+)?: (.*)$")
UNREADABLE = object()  # problem jest już w raporcie; None to poprawny JSON `null`


class ImportFailed(Exception):
  """Bez config.json albo calendar.json nie ma z czego zbudować bazy. To jedyny powód przerwania importu."""


def _read(path: Path, issues: list[Issue]):
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    issues.append(Issue(path.name, None, "missingFile", "rejected", None, "brak pliku"))
  except (json.JSONDecodeError, UnicodeDecodeError) as e:
    issues.append(Issue(path.name, None, "invalidValue", "rejected", None, f"niepoprawny JSON: {e}"))
  return UNREADABLE


def _read_required(src: Path, name: str, model, unwrap=None):
  try:
    raw = json.loads((src / name).read_text(encoding="utf-8"))
    return model.model_validate(raw[unwrap] if unwrap else raw)
  except (OSError, json.JSONDecodeError, KeyError, ValidationError) as e:
    raise ImportFailed(f"{name}: {e}") from e


def _trim_pools(subjects, lecturers, issues: list[Issue]) -> None:
  """Prowadzący spoza bazy wypada z puli. Inaczej przez jedną osobę stracilibyśmy przedmiot i wszystkie jego zajęcia."""
  known = {lec.id for lec in lecturers}
  for s in subjects:
    if not s.lecturer_pools:
      continue
    pools = {form: [i for i in pool if i in known] for form, pool in s.lecturer_pools.items()}
    pools = {form: pool for form, pool in pools.items() if pool}
    if pools != s.lecturer_pools:
      issues.append(Issue("subjects.json", s.id, "cascade", "fixed", "lecturerPools", "z puli usunięto prowadzących spoza bazy"))
      s.lecturer_pools = pools or None


def _drop_repeated_overrides(calendar, issues: list[Issue]) -> None:
  """Dzień ma najwyżej jeden dayOverride, więc zostaje pierwszy."""
  dates = set()
  kept = []
  for exception in calendar.exceptions:
    if isinstance(exception, DayOverride):
      if exception.date in dates:
        issues.append(Issue("calendar.json", exception.id, "duplicateOverride", "rejected", "date",
                            f"drugi dayOverride na {exception.date}: zostaje pierwszy"))
        continue
      dates.add(exception.date)
    kept.append(exception)
  calendar.exceptions[:] = kept


def _resolve_references(parts: dict[str, list], config, calendar, rejected: set[str], issues: list[Issue]) -> Dataset:
  """Usuwa rekordy z wiszącymi odwołaniami, aż dane będą spójne. Usunięcie rekordu może osierocić następne."""
  _drop_repeated_overrides(calendar, issues)
  while True:
    _trim_pools(parts["subjects"], parts["lecturers"], issues)
    ds = Dataset(config=config, calendar=calendar, **parts)
    removed = False
    unresolved = []
    for error in dict.fromkeys(reference_errors(ds)):
      match = REFERENCE_ERROR.match(error)
      if not match:
        unresolved.append(error)
        continue
      name, rid, message = match.groups()
      items = calendar.exceptions if name == "calendar" else parts[name]
      if not any(i.id == rid for i in items):
        continue  # kilka błędów tego samego rekordu w jednej rundzie
      items[:] = [i for i in items if i.id != rid]
      rejected.add(rid)
      removed = True
      kind = "cascade" if message.split()[-1] in rejected else "badReference"
      issues.append(Issue("calendar.json" if name == "calendar" else f"{name}.json", rid, kind, "rejected", None, message))
    if not removed:
      issues += [Issue("references", None, "badReference", "flagged", None, error) for error in unresolved]
      return ds


def _faculty_lists(raw, issues: list[Issue]) -> dict[str, object]:
  """Listy z faculties.json. Zły kształt pliku i brakujące klucze trafiają do raportu."""
  if raw is UNREADABLE:
    return {key: [] for key in FACULTY_LISTS}
  if not isinstance(raw, dict):
    issues.append(Issue("faculties.json", None, "invalidValue", "rejected", None, "plik nie jest obiektem"))
    return {key: [] for key in FACULTY_LISTS}
  for key in FACULTY_LISTS:
    if key not in raw:
      issues.append(Issue("faculties.json", None, "missingField", "rejected", key, f"brak listy {key}"))
  return {key: raw.get(key, []) for key in FACULTY_LISTS}


def clean_dir(src_dir: str | Path, limits: Thresholds = Thresholds()) -> tuple[Dataset, list[Issue]]:
  src = Path(src_dir)
  config = _read_required(src, "config.json", Config)
  calendar = _read_required(src, "calendar.json", Calendar, "calendar")
  issues: list[Issue] = []
  parts: dict[str, list] = {}
  rejected: set[str] = set()

  faculties = _faculty_lists(_read(src / "faculties.json", issues), issues)
  sources = {key: ("faculties.json", raw) for key, raw in faculties.items()}
  for key, name in LIST_FILES.items():
    raw = _read(src / name, issues)
    sources[key] = (name, [] if raw is UNREADABLE else raw)
  for key, (name, raw) in sources.items():
    parts[key], found = clean_records(name, raw, ENTITIES[key], limits)
    issues += found
    rejected |= {i.record_id for i in found if i.action == "rejected" and i.record_id}
    rejected -= {item.id for item in parts[key]}  # duplicateId odrzuca kopię, a pierwszy rekord zostaje

  return _resolve_references(parts, config, calendar, rejected, issues), issues


def import_dir(src_dir: str | Path, engine: Engine, limits: Thresholds = Thresholds()) -> tuple[Dataset, list[Issue]]:
  ds, issues = clean_dir(src_dir, limits)
  write_dataset(engine, ds, issues)
  return ds, issues
