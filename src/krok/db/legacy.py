"""Widok bazy w kształcie starego prototypu. Agent i validate_plan używają tych pól do czasu #13 i #17.

validate_plan rozpoznaje budynki, zajęcia i prowadzących po `name`, więc nazwy zawierają unikalne id.
"""

from krok.model import Dataset


def _buildings(ds: Dataset) -> list[dict]:
  return [{"name": b.id, "rooms": [{"number": r.number, "profession": r.type} for r in ds.rooms if r.building_id == b.id]}
          for b in ds.buildings]


def _groups(ds: Dataset) -> list[dict]:
  subjects = {s.id: s for s in ds.subjects}
  courses: dict[str, list[dict]] = {}
  for c in ds.classes:
    s = subjects[c.subject_id]
    courses.setdefault(c.group_id, []).append(
      {"name": f"{s.name} - {c.form} ({c.id})", "hoursWeekly": c.units_per_week, "profession": c.room_type, "domain": s.competence})
  return [{"name": group, "courses": items} for group, items in courses.items()]


def _workers(ds: Dataset) -> list[dict]:
  return [{"name": f"{lec.name} ({lec.id})", "Profesion": lec.competences, "FavouriteBuilding": lec.home_building_id}
          for lec in ds.lecturers]


VIEWS = {"buildings": _buildings, "student-groups": _groups, "workers": _workers}


def legacy_view(ds: Dataset, name: str) -> list[dict]:
  return VIEWS[name](ds)
