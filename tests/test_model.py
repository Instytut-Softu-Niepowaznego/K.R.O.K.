import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from krok.model import (Availability, Building, Calendar, Class, Config, Faculty, Group, Lecturer, Program, Room, StudyField,
                        Subject, reference_errors)
from krok.generator import generate, load_preset

DOCS = Path(__file__).parent.parent / "docs" / "etap1"


def doc_examples(name: str = "model-domeny.md") -> list[dict]:
  blocks = re.findall(r"```json\n(.*?)```", (DOCS / name).read_text(encoding="utf-8"), re.S)
  return [json.loads(b) for b in blocks if '"..."' not in b]


def test_doc_examples_match_model():
  """Przykłady ze specyfikacji muszą przechodzić walidację — inaczej model i docs się rozjechały."""
  ex = doc_examples()
  Config.model_validate({"timeGrid": ex[1]["timeGrid"], "roomTypes": ["lab-it"], "walkingSpeedKmh": 5, "routeFactor": 1.3,
                         "rules": ex[0]["rules"]})
  Calendar.model_validate(ex[2]["calendar"])
  for cls, key in [(Faculty, "faculties"), (StudyField, "fields"), (Program, "programs"), (Group, "groups")]:
    for item in ex[3][key]:
      cls.model_validate(item)
  Building.model_validate(ex[4])
  Room.model_validate(ex[5])
  Lecturer.model_validate(ex[6])
  Subject.model_validate(ex[7])
  Class.model_validate(ex[8])


def test_criteria_config_matches_model():
  """Pełna lista reguł z kryteriów (#3) przechodzi walidację i jest tą samą listą co w bazowym config.json."""
  crit = doc_examples("kryteria.md")[0]
  cfg = Config.model_validate({"timeGrid": doc_examples()[1]["timeGrid"], "roomTypes": ["lab-it"], **crit})
  base = json.loads((Path(__file__).parent.parent / "src/krok/data/wj/config.json").read_text(encoding="utf-8"))
  assert Config.model_validate(base).rules == cfg.rules
  assert base["routeFactor"] == crit["routeFactor"] and base["walkingSpeedKmh"] == crit["walkingSpeedKmh"]


def test_json_keys_are_camel_case_and_roundtrip():
  lec = Lecturer(id="P-1", name="dr X", faculty_ids=["WJ"], competences=["a"], home_building_id="P1",
                 availability=[Availability(day="Pn", from_="08:00", to="10:00")], max_units_weekly=10)
  data = lec.model_dump(mode="json")
  assert {"homeBuildingId", "maxUnitsWeekly", "facultyIds"} <= set(data)
  assert data["availability"][0] == {"day": "Pn", "from": "08:00", "to": "10:00"}
  assert Lecturer.model_validate(data) == lec


def test_unknown_field_is_rejected():
  with pytest.raises(ValidationError):
    Subject.model_validate({"id": "BD", "name": "Bazy", "competence": "bd", "Profesion": "IT"})


@pytest.mark.parametrize("data", [
  {"unitsPerWeek": 3, "unitsPerMeeting": 2},
  {"unitsPerWeek": 0, "unitsPerMeeting": 1},
])
def test_class_units_rules(data):
  base = {"id": "c", "subjectId": "s", "form": "L", "groupId": "g", "parity": "every", "roomType": "lab-it"}
  with pytest.raises(ValidationError):
    Class.model_validate({**base, **data})


def test_empty_lecturer_pool_is_rejected():
  with pytest.raises(ValidationError):
    Subject.model_validate({"id": "BD", "name": "Bazy", "competence": "bd", "lecturerPools": {"L": []}})


def test_room_id_must_be_building_and_number():
  with pytest.raises(ValidationError):
    Room.model_validate({"id": "101", "buildingId": "P1", "number": "101", "type": "lab-it", "capacity": 16, "facultyIds": ["WJ"]})


@pytest.mark.parametrize("data", [
  {"day": "Pn", "from": "10:00", "to": "09:00"},
  {"day": "Pon", "from": "08:00", "to": "09:00"},
  {"day": "Pn", "from": "8:00", "to": "09:00"},
])
def test_availability_rules(data):
  with pytest.raises(ValidationError):
    Availability.model_validate(data)


def test_group_tree_rules():
  with pytest.raises(ValidationError):
    Group(id="g", program_id="p", kind="cohort", size=10)  # rocznik bez semestru
  with pytest.raises(ValidationError):
    Group(id="g", program_id="p", kind="lab", size=10)  # lab bez rodzica


def test_reference_errors_finds_dangling_duplicate_and_cycle():
  ds = generate(load_preset("mini")).dataset
  assert reference_errors(ds) == []
  ds.classes[0].group_id = "NIE-MA"
  ds.rooms.append(ds.rooms[0])
  a, b = [g for g in ds.groups if g.kind == "exercise"][:2]
  a.parent_id, b.parent_id = b.id, a.id
  errors = "\n".join(reference_errors(ds))
  assert "nieznane groupId NIE-MA" in errors
  assert f"zduplikowane id {ds.rooms[0].id}" in errors
  assert "cykl" in errors


def test_reference_errors_checks_lecturer_pools():
  ds = generate(load_preset("mini")).dataset
  subject = ds.subjects[0]
  outsider = next(lec for lec in ds.lecturers if subject.competence not in lec.competences)
  insider = next(lec for lec in ds.lecturers if subject.competence in lec.competences)
  pinned = next(c for c in ds.classes if c.subject_id == subject.id)
  subject.lecturer_pools = {"P": [outsider.id, "P-9999"], pinned.form: [insider.id]}
  pinned.lecturer_id = outsider.id
  errors = "\n".join(reference_errors(ds))
  assert f"{outsider.id} nie ma kompetencji {subject.competence}" in errors
  assert "nieznane lecturerId P-9999" in errors
  assert f"lecturerId {outsider.id} spoza puli {subject.id}/{pinned.form}" in errors
