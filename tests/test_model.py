import json
import re
from pathlib import Path

from krok.model import Building, Calendar, Class, Config, Faculty, Group, Lecturer, Program, Room, StudyField, Subject, reference_errors
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


def test_reference_errors_finds_broken_references():
  ds = generate(load_preset("mini")).dataset
  ds.classes[0].group_id = "NIE-MA"
  ds.rooms.append(ds.rooms[0])
  a, b = [g for g in ds.groups if g.kind == "exercise"][:2]
  a.parent_id, b.parent_id = b.id, a.id
  subject = ds.subjects[0]
  outsider = next(lec for lec in ds.lecturers if subject.competence not in lec.competences)
  insider = next(lec for lec in ds.lecturers if subject.competence in lec.competences)
  pinned = next(c for c in ds.classes if c.subject_id == subject.id)
  subject.lecturer_pools = {"P": [outsider.id, "P-9999"], pinned.form: [insider.id]}
  pinned.lecturer_id = outsider.id

  errors = "\n".join(reference_errors(ds))
  assert "nieznane groupId NIE-MA" in errors
  assert f"zduplikowane id {ds.rooms[0].id}" in errors
  assert "cykl" in errors
  assert f"{outsider.id} nie ma kompetencji {subject.competence}" in errors
  assert "nieznane lecturerId P-9999" in errors
  assert f"lecturerId {outsider.id} spoza puli {subject.id}/{pinned.form}" in errors
