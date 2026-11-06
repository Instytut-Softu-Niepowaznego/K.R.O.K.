"""Czyszczenie rekordów przed zapisem do bazy. Problem trafia do raportu, a import idzie dalej.

Co się dzieje z rekordem (`Issue.action`):
  fixed     naprawiony albo usunięta zbędna kopia
  rejected  nie trafia do bazy
  flagged   trafia do bazy, ale wartość jest wątpliwa
"""

import difflib
import json
import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from krok.model.base import Model

# Nazwy pól ze starego formatu prototypu.
LEGACY_KEYS = {"Profesion": "competences", "professions": "competences", "FavouriteBuilding": "homeBuildingId"}

# plik -> (pole liczbowe, pole dzielące rekordy na porównywalne grupy)
COMPARED = {"rooms.json": ("capacity", "type"), "groups.json": ("size", "kind"), "lecturers.json": ("max_units_weekly", None),
            "classes.json": ("units_per_week", "form")}


@dataclass
class Issue:
  source: str
  record_id: str | None
  kind: str
  action: str
  field: str | None
  message: str

  def row(self) -> dict:
    d = asdict(self)
    d["recordId"] = d.pop("record_id")
    return d


@dataclass(frozen=True)
class Thresholds:
  max_ratio: float = 4.0  # wartość > max_ratio × mediana podobnych rekordów jest odstająca
  max_coord_delta: float = 0.1  # stopnie od mediany budynków, ok. 11 km


def _fields(model: type[Model]) -> dict[str, bool]:
  """Nazwa pola w JSON -> czy wymagane."""
  return {f.alias or name: f.is_required() for name, f in model.model_fields.items()}


def _normalize_keys(source: str, rec: dict, known: dict[str, bool], issues: list[Issue]) -> dict:
  out = {k: v for k, v in rec.items() if k in known}
  rid = rec.get("id")
  for key, value in rec.items():
    if key in known:
      continue
    free = [f for f in known if f not in out]  # literówka nie może nadpisać istniejącego pola
    lowered = {f.lower(): f for f in free}
    legacy = LEGACY_KEYS.get(key)
    match = legacy if legacy in free else lowered.get(key.lower())
    match = match or next(iter(difflib.get_close_matches(key, free, 1, 0.6)), None)
    if match:
      out[match] = value
      issues.append(Issue(source, rid, "keyTypo", "fixed", match, f"pole {key} -> {match}"))
    else:
      issues.append(Issue(source, rid, "unknownField", "flagged", key, f"nieznane pole {key} pominięte"))
  return out


def _validation_issue(source: str, rid: str | None, error: dict) -> Issue:
  field = ".".join(str(p) for p in error["loc"]) or None
  kind = error["type"]
  if kind == "missing":
    return Issue(source, rid, "missingField", "rejected", field, f"brak wymaganego pola {field}")
  label = "outlier" if kind.startswith(("greater_than", "less_than")) else "invalidValue"
  return Issue(source, rid, label, "rejected", field, f"{field or 'rekord'}: {error['msg']}")


def _needed_optional_field(source: str, rec: dict) -> str | None:
  """Pole opcjonalne w modelu, ale wymagane w tym rekordzie. Tylko grupa ma takie warunki (Group._check)."""
  if source != "groups.json":
    return None
  return "semester" if rec.get("kind") == "cohort" else "parentId"


def _is_copy_id(original: str, rid: str) -> bool:
  return re.fullmatch(re.escape(original) + r"[-_ ]\d+", rid) is not None


def _flag_outliers(source: str, items: list[Model], limits: Thresholds, issues: list[Issue]) -> None:
  if source == "buildings.json":
    for attr in ("lat", "lon"):
      center = statistics.median(getattr(i, attr) for i in items) if items else 0
      for i in items:
        if abs(getattr(i, attr) - center) > limits.max_coord_delta:
          issues.append(Issue(source, i.id, "outlier", "flagged", attr, f"{attr}={getattr(i, attr)} daleko od pozostałych budynków"))
  if source not in COMPARED:
    return
  attr, bucket_attr = COMPARED[source]
  buckets = defaultdict(list)
  for i in items:
    buckets[getattr(i, bucket_attr) if bucket_attr else None].append(i)
  for bucket in buckets.values():
    limit = limits.max_ratio * statistics.median(getattr(i, attr) for i in bucket)
    for i in bucket:
      value = getattr(i, attr)
      if value == 0 or value > limit:
        issues.append(Issue(source, i.id, "outlier", "flagged", attr, f"{attr}={value} odstaje od podobnych rekordów (próg {limit:g})"))


def clean_records(source: str, raw: object, model: type[Model], limits: Thresholds = Thresholds()) -> tuple[list[Model], list[Issue]]:
  """Rekordy z jednego pliku: normalizacja kluczy, duplikaty, walidacja modelem, wartości odstające."""
  issues: list[Issue] = []
  if not isinstance(raw, list):
    return [], [Issue(source, None, "invalidValue", "rejected", None, "plik nie jest listą rekordów")]
  known = _fields(model)
  by_id: dict[str, str] = {}
  by_content: dict[str, list[str]] = defaultdict(list)
  items: list[Model] = []
  for rec in raw:
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
      issues.append(Issue(source, None, "missingField", "rejected", "id", f"rekord bez id: {str(rec)[:80]}"))
      continue
    rid = rec["id"]
    clean = _normalize_keys(source, rec, known, issues)

    full = json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
    content = json.dumps({k: v for k, v in clean.items() if k != "id"}, sort_keys=True, ensure_ascii=False, default=str)
    if rid in by_id:
      same = by_id[rid] == full
      issues.append(Issue(source, rid, "exactDuplicate" if same else "duplicateId", "fixed" if same else "rejected", "id",
                          "druga taka sama kopia rekordu" if same else "ten sam id, inna treść: zostaje pierwszy rekord"))
      continue
    # Grupy rodzeństwa mogą mieć identyczną treść, więc dla grup liczy się też podobieństwo id.
    first = next((i for i in by_content[content] if source != "groups.json" or _is_copy_id(i, rid)), None)
    if first is not None:
      issues.append(Issue(source, rid, "nearDuplicate", "fixed", "id", f"kopia rekordu {first} pod innym id"))
      continue

    missing = [field for field, required in known.items() if not required and field not in clean]
    try:
      item = model.model_validate(clean)
    except ValidationError as e:
      error = e.errors()[0]
      cause = _needed_optional_field(source, clean)
      if cause in missing and not error["loc"]:
        issues.append(Issue(source, rid, "missingField", "rejected", cause, f"brak pola {cause}: {error['msg']}"))
      else:
        issues.append(_validation_issue(source, rid, error))
      continue
    issues += [Issue(source, rid, "missingField", "flagged", field, f"brak pola {field}, przyjęto wartość domyślną") for field in missing]
    # Dopiero poprawny rekord zajmuje id: błędny nie może zablokować poprawnego o tym samym id.
    by_id[rid] = full
    by_content[content].append(rid)
    items.append(item)
  _flag_outliers(source, items, limits, issues)
  return items, issues
