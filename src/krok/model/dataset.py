"""Komplet danych wejściowych: wczytywanie, zapis i spójność referencji.

Układ plików (docs/etap1/model-domeny.md, „Wejścia”):
  config.json      obiekt Config
  calendar.json    {"calendar": {...}}
  faculties.json   {"faculties": [...], "fields": [...], "programs": [...]}
  pozostałe        tablice encji
"""

import json
from collections import Counter
from pathlib import Path

from .base import Model
from .calendar import Calendar, DayOverride
from .config import Config
from .resources import Building, Lecturer, Room
from .structure import Faculty, Group, Program, StudyField
from .teaching import Class, Subject

LIST_FILES = {
  "groups": "groups.json",
  "buildings": "buildings.json",
  "rooms": "rooms.json",
  "lecturers": "lecturers.json",
  "subjects": "subjects.json",
  "classes": "classes.json",
}
FILES = ["config.json", "calendar.json", "faculties.json", *LIST_FILES.values()]


class Dataset(Model):
  config: Config
  calendar: Calendar
  faculties: list[Faculty]
  fields: list[StudyField]
  programs: list[Program]
  groups: list[Group]
  buildings: list[Building]
  rooms: list[Room]
  lecturers: list[Lecturer]
  subjects: list[Subject]
  classes: list[Class]

  def files(self) -> dict[str, object]:
    """Zawartość każdego pliku jako dane gotowe do json.dump."""
    d = self.model_dump(mode="json")
    out: dict[str, object] = {
      "config.json": d["config"],
      "calendar.json": {"calendar": d["calendar"]},
      "faculties.json": {k: d[k] for k in ("faculties", "fields", "programs")},
    }
    out.update({name: d[key] for key, name in LIST_FILES.items()})
    return out


def dump_json(data: object) -> str:
  return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def save_dataset(ds: Dataset, out_dir: Path) -> None:
  out_dir.mkdir(parents=True, exist_ok=True)
  for name, data in ds.files().items():
    (out_dir / name).write_text(dump_json(data), encoding="utf-8")


class DatasetError(ValueError):
  """Pliku danych nie ma, nie da się go odczytać (uprawnienia, katalog, kodowanie, zły JSON) albo ma zły kształt.

  Problemy z treścią rekordów zgłasza dalej walidacja modelu (pydantic.ValidationError).
  """


def read_json_file(path: Path) -> object:
  """Czyta plik JSON. Każdy błąd odczytu zamienia na DatasetError z nazwą pliku."""
  try:
    return json.loads(Path(path).read_text(encoding="utf-8"))
  except FileNotFoundError as e:
    raise DatasetError(f"brak pliku {path}") from e
  except UnicodeDecodeError as e:
    raise DatasetError(f"{path}: plik nie jest w UTF-8 ({e.reason})") from e
  except json.JSONDecodeError as e:
    raise DatasetError(f"{path}: niepoprawny JSON ({e.msg}, linia {e.lineno})") from e
  except OSError as e:  # katalog zamiast pliku, brak uprawnień
    raise DatasetError(f"{path}: nie można odczytać ({e.strerror or e})") from e


def read_files(src_dir: Path) -> dict[str, object]:
  return {name: read_json_file(Path(src_dir) / name) for name in FILES}


def _expect(name: str, value, kind: type, what: str):
  if not isinstance(value, kind):
    raise DatasetError(f"{name}: oczekiwano {what}, a jest {type(value).__name__}")
  return value


def dataset_from_files(files: dict[str, object]) -> Dataset:
  """Składa Dataset z plików. Zły kształt pliku → DatasetError, złe rekordy → ValidationError."""
  calendar = _expect("calendar.json", files["calendar.json"], dict, 'obiektu {"calendar": {...}}')
  faculties = _expect("faculties.json", files["faculties.json"], dict, "obiektu {faculties, fields, programs}")
  data = {
    "config": _expect("config.json", files["config.json"], dict, "obiektu"),
    "calendar": _expect("calendar.json", calendar.get("calendar"), dict, 'obiektu w polu "calendar"'),
    **{key: _expect(f"faculties.json/{key}", faculties.get(key), list, "listy") for key in ("faculties", "fields", "programs")},
    **{key: _expect(name, files[name], list, "listy rekordów") for key, name in LIST_FILES.items()},
  }
  return Dataset.model_validate(data)


def load_dataset(src_dir: Path) -> Dataset:
  return dataset_from_files(read_files(Path(src_dir)))


def reference_errors(ds: Dataset) -> list[str]:
  """Błędy spójności, których nie widać w pojedynczej encji: duplikaty id, wiszące odwołania, cykle."""
  errors: list[str] = []

  def unique(name: str, items: list) -> set[str]:
    counts = Counter(i.id for i in items)
    errors.extend(f"{name}: zduplikowane id {i}" for i, n in counts.items() if n > 1)
    return set(counts)

  faculties = unique("faculties", ds.faculties)
  fields = unique("fields", ds.fields)
  programs = unique("programs", ds.programs)
  groups = {g.id: g for g in ds.groups}
  unique("groups", ds.groups)
  buildings = unique("buildings", ds.buildings)
  rooms = unique("rooms", ds.rooms)
  lecturers = unique("lecturers", ds.lecturers)
  subjects = {s.id: s for s in ds.subjects}
  unique("subjects", ds.subjects)
  unique("classes", ds.classes)
  room_types = set(ds.config.room_types)

  def ref(where: str, kind: str, value: str | None, known) -> None:
    if value is not None and value not in known:
      errors.append(f"{where}: nieznane {kind} {value}")

  for f in ds.fields:
    ref(f"fields/{f.id}", "facultyId", f.faculty_id, faculties)
  for p in ds.programs:
    ref(f"programs/{p.id}", "fieldId", p.field_id, fields)
  for g in ds.groups:
    ref(f"groups/{g.id}", "programId", g.program_id, programs)
    ref(f"groups/{g.id}", "parentId", g.parent_id, groups)
    parent = groups.get(g.parent_id) if g.parent_id else None
    if parent and parent.program_id != g.program_id:
      errors.append(f"groups/{g.id}: rodzic {parent.id} z innego programu")
    seen, cur = {g.id}, parent
    while cur:
      if cur.id in seen:
        errors.append(f"groups/{g.id}: cykl w drzewie grup")
        break
      seen.add(cur.id)
      cur = groups.get(cur.parent_id) if cur.parent_id else None
  for b in ds.buildings:
    for fid in b.faculty_ids:
      ref(f"buildings/{b.id}", "facultyId", fid, faculties)
  for r in ds.rooms:
    ref(f"rooms/{r.id}", "buildingId", r.building_id, buildings)
    ref(f"rooms/{r.id}", "type", r.type, room_types)
    for fid in r.faculty_ids:
      ref(f"rooms/{r.id}", "facultyId", fid, faculties)
  for lec in ds.lecturers:
    ref(f"lecturers/{lec.id}", "homeBuildingId", lec.home_building_id, buildings)
    for fid in lec.faculty_ids:
      ref(f"lecturers/{lec.id}", "facultyId", fid, faculties)
  lecturer_by_id = {lec.id: lec for lec in ds.lecturers}
  for s in ds.subjects:
    for form, pool in (s.lecturer_pools or {}).items():
      for lid in pool:
        ref(f"subjects/{s.id}/lecturerPools/{form}", "lecturerId", lid, lecturers)
        if lid in lecturer_by_id and s.competence not in lecturer_by_id[lid].competences:
          errors.append(f"subjects/{s.id}/lecturerPools/{form}: {lid} nie ma kompetencji {s.competence}")
      if len(set(pool)) != len(pool):
        errors.append(f"subjects/{s.id}/lecturerPools/{form}: powtórzony prowadzący")
  for c in ds.classes:
    ref(f"classes/{c.id}", "subjectId", c.subject_id, subjects)
    pool = subjects[c.subject_id].pool(c.form) if c.subject_id in subjects else None
    if c.lecturer_id and pool is not None and c.lecturer_id not in pool:
      errors.append(f"classes/{c.id}: lecturerId {c.lecturer_id} spoza puli {c.subject_id}/{c.form}")
    ref(f"classes/{c.id}", "groupId", c.group_id, groups)
    ref(f"classes/{c.id}", "roomType", c.room_type, room_types)
    ref(f"classes/{c.id}", "lecturerId", c.lecturer_id, lecturers)
  for e in ds.calendar.exceptions:
    if e.scope is None:
      continue
    for kind, ids, known in [
      ("facultyId", e.scope.faculty_ids, faculties),
      ("groupId", e.scope.group_ids, groups),
      ("roomId", e.scope.room_ids, rooms),
      ("lecturerId", e.scope.lecturer_ids, lecturers),
    ]:
      for i in ids or []:
        ref(f"calendar/{e.id}", kind, i, known)
  overrides = Counter(e.date for e in ds.calendar.exceptions if isinstance(e, DayOverride))
  errors.extend(f"calendar: dwa dayOverride na {d}" for d, n in overrides.items() if n > 1)
  return errors
