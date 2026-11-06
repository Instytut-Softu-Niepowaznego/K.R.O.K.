"""Import katalogu z plikami JSON lub CSV do bazy. Zaszumione dane nie przechodzą przez load_dataset, więc rekordy czyścimy osobno."""

import csv
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import get_origin

from sqlalchemy import Engine

from krok.model import Calendar, Config, Dataset, DayOverride, reference_errors
from krok.model.dataset import LIST_FILES

from krok.model.base import Model

from .clean import Issue, Thresholds, clean_records, field_requirements
from .schema import ENTITIES, write_dataset

FACULTY_LISTS = ["faculties", "fields", "programs"]
REFERENCE_ERROR = re.compile(r"^(\w+)/(.+?)(?:/lecturerPools/\w+)?: (.*)$")
UNREADABLE = object()  # problem jest już w raporcie; None to poprawny JSON `null`


class ImportFailed(Exception):
  """Bez config.json albo calendar.json nie ma z czego zbudować bazy. To jedyny powód przerwania importu."""


def _load_json(path: Path):
  """Czyta plik JSON."""
  return json.loads(path.read_text(encoding="utf-8"))


def _csv_cell(value: str | None, is_list: bool):
  """Komórka CSV: JSON dla pól zagnieżdżonych, `;` między elementami prostej listy, inaczej tekst."""
  text = (value or "").strip()
  if text[:1] in ("[", "{"):
    try:
      return json.loads(text)
    except ValueError:
      return text  # zły JSON odrzuci walidacja modelu i trafi do raportu
  if is_list:
    return [item.strip() for item in text.split(";") if item.strip()]
  return text


def read_csv_records(path: Path, model: type[Model]) -> list[dict]:
  """Wiersze CSV jako rekordy dla clean_records: nazwy kolumn to pola JSON, liczby zamienia model.

  Pusta komórka to `null` w polu opcjonalnym i brak pola w wymaganym. BOM z Excela jest pomijany.
  """
  fields = field_requirements(model)
  lists = {f.alias or name for name, f in model.model_fields.items() if get_origin(f.annotation) is list}
  records = []
  with path.open(encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f, restkey="_extra"):  # nadmiarowe komórki wierszy raportuje czyszczenie jako nieznane pole
      record = {}
      for key, value in row.items():
        if key not in fields:
          record[key] = value  # literówkę w nazwie kolumny naprawia czyszczenie
          continue
        cell = _csv_cell(value, key in lists)
        if cell != "":
          record[key] = cell
        elif not fields[key]:
          record[key] = None
      records.append(record)
  return records


def _read(path: Path, issues: list[Issue], load: Callable[[Path], object] = _load_json):
  """Czyta plik. Błąd trafia do raportu, a wynikiem jest `UNREADABLE`."""
  try:
    return load(path)
  except FileNotFoundError:
    issues.append(Issue(path.name, None, "missingFile", "rejected", None, "brak pliku"))
  except OSError as e:
    issues.append(Issue(path.name, None, "invalidValue", "rejected", None, f"nie można odczytać pliku: {e}"))
  except (ValueError, csv.Error) as e:  # ValueError: zły JSON albo UTF-8
    issues.append(Issue(path.name, None, "invalidValue", "rejected", None, f"niepoprawny plik: {e}"))
  return UNREADABLE


def _read_required(src: Path, name: str, model, unwrap=None):
  """Czyta plik, bez którego nie ma importu. Każdy błąd to `ImportFailed`."""
  try:
    raw = json.loads((src / name).read_text(encoding="utf-8"))
    return model.model_validate(raw[unwrap] if unwrap else raw)
  except (OSError, ValueError, KeyError, TypeError) as e:  # ValueError: zły JSON, UTF-8 i walidacja modelu
    raise ImportFailed(f"{name}: {e}") from e


def _read_list(src: Path, name: str, model: type[Model], issues: list[Issue]):
  """Plik listy: JSON, a gdy go nie ma, CSV o tej samej nazwie."""
  json_path, csv_path = src / name, (src / name).with_suffix(".csv")
  if csv_path.exists() and not json_path.exists():
    return _read(csv_path, issues, lambda path: read_csv_records(path, model))
  return _read(json_path, issues)


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
  """Czyści katalog z danymi i zwraca spójny `Dataset` oraz listę problemów."""
  src = Path(src_dir)
  config = _read_required(src, "config.json", Config)
  calendar = _read_required(src, "calendar.json", Calendar, "calendar")
  issues: list[Issue] = []
  parts: dict[str, list] = {}
  rejected: set[str] = set()

  faculties = _faculty_lists(_read(src / "faculties.json", issues), issues)
  sources = {key: ("faculties.json", raw) for key, raw in faculties.items()}
  for key, name in LIST_FILES.items():
    raw = _read_list(src, name, ENTITIES[key], issues)
    sources[key] = (name, [] if raw is UNREADABLE else raw)
  for key, (name, raw) in sources.items():
    parts[key], found = clean_records(name, raw, ENTITIES[key], limits)
    issues += found
    rejected |= {i.record_id for i in found if i.action == "rejected" and i.record_id}
    rejected -= {item.id for item in parts[key]}  # duplicateId odrzuca kopię, a pierwszy rekord zostaje

  return _resolve_references(parts, config, calendar, rejected, issues), issues


def import_dir(src_dir: str | Path, engine: Engine, limits: Thresholds = Thresholds()) -> tuple[Dataset, list[Issue]]:
  """Czyści katalog i zastępuje nim zawartość bazy."""
  ds, issues = clean_dir(src_dir, limits)
  write_dataset(engine, ds, issues)
  return ds, issues
