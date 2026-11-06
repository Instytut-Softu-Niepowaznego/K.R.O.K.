import json
from pathlib import Path

import pytest
from sqlalchemy import select

from krok.db import ImportFailed, apply_events, build_report, connect, import_dir, read_dataset, read_issues, write_dataset
from krok.db.cli import main
from krok.db.clean import clean_records
from krok.db.legacy import legacy_view
from krok.db.schema import event_log
from krok.events import generate_events
from krok.generator import generate, load_preset, write
from krok.harness.plan import Lesson, validate
from krok.harness.tools.get_data import load
from krok.model import Class, Group, Lecturer, reference_errors

# Literówka w odwołaniu może trafić w inny istniejący id, więc tego nie da się wykryć.
REFERENCE_TARGETS = {"programId": "programs", "parentId": "groups", "buildingId": "buildings", "homeBuildingId": "buildings",
                     "subjectId": "subjects", "groupId": "groups", "lecturerId": "lecturers"}
# Rekord z noise może też odpaść z powodu innego rekordu, a sala ze złym buildingId nie przechodzi walidacji id.
EXPECTED_KINDS = {"keyTypo": {"keyTypo"}, "missingField": {"missingField"}, "outlier": {"outlier"},
                  "badReference": {"badReference", "invalidValue", "cascade"}, "exactDuplicate": {"exactDuplicate"},
                  "nearDuplicate": {"nearDuplicate"}}
EXPECTED_ACTIONS = {"keyTypo": {"fixed"}, "missingField": {"rejected", "flagged"}, "outlier": {"rejected", "flagged"},
                    "badReference": {"rejected"}, "exactDuplicate": {"fixed"}, "nearDuplicate": {"fixed"}}
FIELD_CHECKED = {"keyTypo", "missingField", "outlier"}


def same_field(issue_field: str | None, field: str) -> bool:
  return (issue_field or "").replace("_", "").lower() == field.lower()


@pytest.fixture
def engine(tmp_path):
  e = connect(tmp_path / "krok.db", create=True)
  yield e
  e.dispose()


def generated(tmp_path: Path, name: str, *, noise: bool = False, seed: int | None = None, edge: bool | None = None) -> Path:
  preset = load_preset(name)
  preset.noise.enabled = noise
  if edge is not None:
    preset.edge_cases.enabled = edge
  out = tmp_path / f"{name}-{seed}"
  write(generate(preset, seed), out)
  return out


@pytest.mark.parametrize("name", ["mini", "kierunek"])
def test_clean_data_imports_without_issues_and_roundtrips(tmp_path, engine, name):
  preset = load_preset(name)
  preset.noise.enabled = False
  expected = generate(preset).dataset
  src = tmp_path / "src"
  write(generate(preset), src)

  _, issues = import_dir(src, engine)

  assert issues == []
  assert read_dataset(engine) == expected


def test_edge_cases_keep_all_records(tmp_path, engine):
  src = generated(tmp_path, "edge-cases")
  ds, issues = import_dir(src, engine)
  assert [i for i in issues if i.action == "rejected"] == []
  assert reference_errors(ds) == []


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("name", ["mini", "kierunek"])
def test_noise_is_detected_and_database_stays_consistent(tmp_path, engine, name, seed):
  src = generated(tmp_path, name, noise=True, seed=seed)
  manifest = json.loads((src / "_noise.json").read_text(encoding="utf-8"))
  manifest = manifest["changes"] if isinstance(manifest, dict) else manifest
  assert manifest

  ds, issues = import_dir(src, engine)

  for change in manifest:
    # Dla duplikatu z nowym id problem jest zapisany przy kopii.
    record = change["after"] if change["kind"] == "nearDuplicate" else change["id"]
    found = [i for i in issues if (i.source, i.record_id) == (change["file"], record) and i.kind in EXPECTED_KINDS[change["kind"]]
             and i.action in EXPECTED_ACTIONS[change["kind"]]]
    if change["kind"] in FIELD_CHECKED:
      found = [i for i in found if same_field(i.field, change["field"])]
    if not found:
      assert change["kind"] == "badReference", change
      target = {x.id for x in getattr(ds, REFERENCE_TARGETS[change["field"]])}
      assert change["after"] in target, change
  assert reference_errors(ds) == []
  assert read_dataset(engine) == ds


def test_key_typo_is_repaired_without_losing_the_record():
  raw = [{"id": "G", "programId": "P", "parentId": "R", "kid": "lab", "semester": 1, "Size": 12}]
  items, issues = clean_records("groups.json", raw, Group)
  assert (items[0].kind, items[0].size) == ("lab", 12)
  assert sorted(i.field for i in issues) == ["kind", "size"]
  assert {i.action for i in issues} == {"fixed"}


def test_legacy_field_names_are_normalized():
  raw = [{"id": "X", "name": "dr X", "facultyIds": ["WJ"], "Profesion": ["IT"], "FavouriteBuilding": "P1", "availability": [],
          "maxUnitsWeekly": 10}]
  items, _ = clean_records("lecturers.json", raw, Lecturer)
  assert items[0].competences == ["IT"]
  assert items[0].home_building_id == "P1"


def test_sibling_groups_with_identical_content_are_not_duplicates():
  base = {"programId": "P", "parentId": "R", "kind": "lab", "semester": 1, "size": 12}
  items, issues = clean_records("groups.json", [{"id": "L1", **base}, {"id": "L2", **base}, {"id": "L1-2", **base}], Group)
  assert [i.id for i in items] == ["L1", "L2"]
  assert [(i.record_id, i.kind) for i in issues] == [("L1-2", "nearDuplicate")]


def test_same_id_with_other_content_keeps_first():
  base = {"programId": "P", "parentId": "R", "kind": "lab", "semester": 1}
  items, issues = clean_records("groups.json", [{"id": "L", **base, "size": 12}, {"id": "L", **base, "size": 15}], Group)
  assert items[0].size == 12
  assert issues[0].kind == "duplicateId"


def test_invalid_record_does_not_block_valid_record_with_same_id():
  base = {"programId": "P", "parentId": "R", "kind": "lab", "semester": 1}
  items, issues = clean_records("groups.json", [{"id": "L", **base, "size": -1}, {"id": "L", **base, "size": 12}], Group)
  assert [i.size for i in items] == [12]
  assert [i.kind for i in issues] == ["outlier"]


def test_rejected_record_is_not_counted_as_flagged():
  raw = [{"id": "L", "programId": "P", "kind": "lab", "semester": 1, "size": 12}]
  items, issues = clean_records("groups.json", raw, Group)
  assert items == []
  assert [(i.kind, i.action, i.field) for i in issues] == [("missingField", "rejected", "parentId")]


def test_cohort_is_rejected_for_missing_semester_not_parent():
  raw = [{"id": "R", "programId": "P", "kind": "cohort", "size": 60}]
  items, issues = clean_records("groups.json", raw, Group)
  assert items == []
  assert [(i.kind, i.action, i.field) for i in issues] == [("missingField", "rejected", "semester")]


def test_model_error_is_not_blamed_on_missing_optional_field():
  raw = [{"id": "C", "subjectId": "S", "form": "L", "groupId": "G", "unitsPerWeek": 3, "unitsPerMeeting": 2, "parity": "every",
          "roomType": "lab-it"}]
  items, issues = clean_records("classes.json", raw, Class)
  assert items == []
  assert [(i.kind, i.action) for i in issues] == [("invalidValue", "rejected")]
  assert "podzielne" in issues[0].message


def test_kept_record_with_missing_optional_field_is_flagged():
  raw = [{"id": "R", "programId": "P", "kind": "cohort", "semester": 1, "size": 60}]
  items, issues = clean_records("groups.json", raw, Group)
  assert [i.id for i in items] == ["R"]
  assert [(i.kind, i.action, i.field) for i in issues] == [("missingField", "flagged", "parentId")]


def test_rejected_group_takes_its_classes_and_children(tmp_path, engine):
  src = generated(tmp_path, "mini")
  groups = json.loads((src / "groups.json").read_text(encoding="utf-8"))
  parent = next(g for g in groups if g["kind"] == "lecture")
  children = [g["id"] for g in groups if g["parentId"] == parent["id"]]
  assert children
  parent["size"] = -1
  (src / "groups.json").write_text(json.dumps(groups), encoding="utf-8")

  ds, issues = import_dir(src, engine)

  assert parent["id"] not in {g.id for g in ds.groups}
  assert not set(children) & {g.id for g in ds.groups}
  assert {"outlier", "cascade"} <= {i.kind for i in issues}
  assert reference_errors(ds) == []


def test_missing_lecturer_only_leaves_pool(tmp_path, engine):
  src = generated(tmp_path, "mini")
  subjects = json.loads((src / "subjects.json").read_text(encoding="utf-8"))
  subject = next(s for s in subjects if s["lecturerPools"])
  form, pool = next(iter(subject["lecturerPools"].items()))
  lecturers = [x for x in json.loads((src / "lecturers.json").read_text(encoding="utf-8")) if x["id"] != pool[0]]
  (src / "lecturers.json").write_text(json.dumps(lecturers), encoding="utf-8")

  ds, issues = import_dir(src, engine)

  kept = next(s for s in ds.subjects if s.id == subject["id"])
  assert pool[0] not in (kept.pool(form) or [])
  assert any(i.kind == "cascade" and i.record_id == subject["id"] for i in issues)


def test_missing_list_file_is_reported_not_fatal(tmp_path, engine):
  src = generated(tmp_path, "mini")
  (src / "classes.json").unlink()
  ds, issues = import_dir(src, engine)
  assert ds.classes == []
  assert ("classes.json", "missingFile") in {(i.source, i.kind) for i in issues}


def test_invalid_faculties_shape_is_reported(tmp_path, engine):
  src = generated(tmp_path, "mini")
  (src / "faculties.json").write_text("[1]", encoding="utf-8")
  ds, issues = import_dir(src, engine)
  assert ds.faculties == []
  assert ("faculties.json", "invalidValue") in {(i.source, i.kind) for i in issues}


def test_missing_faculty_list_is_reported(tmp_path, engine):
  src = generated(tmp_path, "mini")
  faculties = json.loads((src / "faculties.json").read_text(encoding="utf-8"))
  del faculties["programs"]
  (src / "faculties.json").write_text(json.dumps(faculties), encoding="utf-8")
  _, issues = import_dir(src, engine)
  assert ("faculties.json", "missingField", "programs") in {(i.source, i.kind, i.field) for i in issues}


@pytest.mark.parametrize("content", ["null", "{}", "0"])
def test_list_file_with_wrong_shape_is_reported(tmp_path, engine, content):
  src = generated(tmp_path, "mini")
  (src / "rooms.json").write_text(content, encoding="utf-8")
  ds, issues = import_dir(src, engine)
  assert ds.rooms == []
  assert ("rooms.json", "invalidValue") in {(i.source, i.kind) for i in issues}


def test_second_day_override_is_dropped_once(tmp_path, engine):
  src = generated(tmp_path, "mini")
  calendar = json.loads((src / "calendar.json").read_text(encoding="utf-8"))
  override = {"type": "dayOverride", "date": "2026-11-14", "followsDay": "Pn", "followsParity": None, "scope": None, "label": ""}
  calendar["calendar"]["exceptions"] += [{"id": "O1", **override}, {"id": "O2", **override}]
  (src / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
  groups = json.loads((src / "groups.json").read_text(encoding="utf-8"))
  next(g for g in groups if g["kind"] == "lecture")["size"] = -1  # wymusza kilka rund usuwania
  (src / "groups.json").write_text(json.dumps(groups), encoding="utf-8")

  ds, issues = import_dir(src, engine)

  assert reference_errors(ds) == []
  assert [i.record_id for i in issues if i.kind == "duplicateOverride"] == ["O2"]
  assert not [i for i in issues if i.source == "references"]
  assert {"O1"} <= {x.id for x in read_dataset(engine).calendar.exceptions}


def test_failed_replacement_keeps_previous_data_and_event_log(tmp_path, engine):
  src, stream = stream_for("mini", tmp_path, 7)
  ds, _ = import_dir(src, engine)
  apply_events(engine, stream.lines)
  before = read_dataset(engine)
  with engine.connect() as conn:
    logged = conn.execute(select(event_log)).all()

  broken = ds.model_copy(deep=True)
  broken.groups[0].size = 10**30  # poprawne dla modelu, za duże dla SQLite
  with pytest.raises(OverflowError, match="too large"):
    write_dataset(engine, broken, [])

  assert read_dataset(engine) == before
  with engine.connect() as conn:
    assert conn.execute(select(event_log)).all() == logged


def test_failure_rolls_back_all_events_and_their_log(tmp_path, engine):
  ds, _ = import_dir(generated(tmp_path, "mini"), engine)
  group = ds.groups[0]

  def lines():
    yield json.dumps({"id": "E-1", "timestamp": "2026-10-01T10:00:00", "type": "groupSizeChanged",
                      "payload": {"groupId": group.id, "size": group.size + 1, "effectiveFrom": "2026-10-05"}})
    raise RuntimeError("przerwany strumień")

  with pytest.raises(RuntimeError):
    apply_events(engine, lines())

  assert next(g for g in read_dataset(engine).groups if g.id == group.id).size == group.size
  with engine.connect() as conn:
    assert conn.execute(select(event_log)).all() == []


def test_unreadable_list_file_is_reported(tmp_path, engine):
  src = generated(tmp_path, "mini")
  (src / "rooms.json").unlink()
  (src / "rooms.json").mkdir()
  ds, issues = import_dir(src, engine)
  assert ds.rooms == []
  assert ("rooms.json", "invalidValue") in {(i.source, i.kind) for i in issues}


@pytest.mark.parametrize("name, content", [("config.json", b"\xff"), ("calendar.json", b"\xff"), ("calendar.json", b"[1]"),
                                           ("calendar.json", b'"tekst"'), ("config.json", b"[1]")])
def test_broken_required_file_stops_import_cleanly(tmp_path, engine, name, content):
  src = generated(tmp_path, "mini")
  (src / name).write_bytes(content)
  with pytest.raises(ImportFailed, match=name):
    import_dir(src, engine)


def test_missing_config_stops_import(tmp_path, engine):
  src = generated(tmp_path, "mini")
  (src / "config.json").unlink()
  with pytest.raises(ImportFailed):
    import_dir(src, engine)


def stream_for(name: str, tmp_path: Path, seed: int):
  preset = load_preset(name)
  preset.noise.enabled = False
  preset.events.per_week = 12
  preset.events.error_rate = 0.4
  ds = generate(preset, seed).dataset
  src = tmp_path / f"{name}-events-{seed}"
  write(generate(preset, seed), src)
  return src, generate_events(ds, preset, seed)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_broken_event_line_is_caught(tmp_path, engine, seed):
  src, stream = stream_for("kierunek", tmp_path, seed)
  import_dir(src, engine)

  stats = apply_events(engine, stream.lines)

  with engine.connect() as conn:
    rows = {r.line: r for r in conn.execute(select(event_log))}
  for entry in stream.manifest:
    assert rows[entry["line"]].kind == entry["kind"], entry
  broken = {e["line"] for e in stream.manifest}
  assert all(r.kind is None and r.status == "applied" for line, r in rows.items() if line not in broken)
  assert sum(stats.values()) == len(stream.lines)
  assert reference_errors(read_dataset(engine)) == []


def test_events_change_state(tmp_path, engine):
  src = generated(tmp_path, "mini")
  ds, _ = import_dir(src, engine)
  group, room, lecturer = ds.groups[0], ds.rooms[0], ds.lecturers[0]
  class_ = ds.classes[0].model_dump(mode="json")
  lines = [
    {"id": "E-1", "timestamp": "2026-10-01T10:00:00", "type": "groupSizeChanged",
     "payload": {"groupId": group.id, "size": group.size + 1, "effectiveFrom": "2026-10-05"}},
    {"id": "E-2", "timestamp": "2026-10-02T10:00:00", "type": "roomUnavailable",
     "payload": {"roomId": room.id, "from": "2026-10-05", "to": "2026-10-06", "reason": "remont"}},
    {"id": "E-3", "timestamp": "2026-10-03T10:00:00", "type": "lecturerAbsent",
     "payload": {"lecturerId": lecturer.id, "from": "2026-10-05", "to": "2026-10-06", "reason": "L4"}},
    {"id": "E-4", "timestamp": "2026-10-04T10:00:00", "type": "lecturerAvailabilityChanged",
     "payload": {"lecturerId": lecturer.id, "effectiveFrom": "2026-10-05", "availability": [{"day": "Pn", "from": "08:00", "to": "12:00"}]}},
    {"id": "E-5", "timestamp": "2026-10-05T10:00:00", "type": "subjectAdded",
     "payload": {"subject": {"id": "NOWY", "name": "Nowy", "competence": "x", "lecturerPools": None},
                 "classes": [{**class_, "id": "NOWY-W", "subjectId": "NOWY", "lecturerId": None}]}},
  ]
  assert apply_events(engine, [json.dumps(x) for x in lines])["applied"] == 5

  after = read_dataset(engine)
  assert next(g for g in after.groups if g.id == group.id).size == group.size + 1
  blocks = {x.id: x for x in after.calendar.exceptions}
  assert blocks["E-2"].scope.room_ids == [room.id]
  assert blocks["E-3"].scope.lecturer_ids == [lecturer.id]
  assert next(x for x in after.lecturers if x.id == lecturer.id).availability[0].to == "12:00"
  assert "NOWY-W" in {c.id for c in after.classes}


def test_replaying_stream_changes_nothing(tmp_path, engine):
  src, stream = stream_for("mini", tmp_path, 7)
  import_dir(src, engine)
  apply_events(engine, stream.lines)
  before = read_dataset(engine)

  stats = apply_events(engine, stream.lines)

  assert stats["applied"] == 0
  assert read_dataset(engine) == before


def test_report_counts_noise(tmp_path, engine):
  import_dir(generated(tmp_path, "kierunek", noise=True, seed=1), engine)
  report = build_report(engine)
  assert report["issues"]["total"] == len(read_issues(engine)) > 0
  assert report["records"]["rooms"] > 0
  assert all(len(x) <= 3 for x in report["issues"]["examples"].values())


def test_cli_import_events_report(tmp_path, capsys):
  db = tmp_path / "cli.db"
  src, stream = stream_for("mini", tmp_path, 7)
  events = tmp_path / "events.jsonl"
  events.write_text("\n".join(stream.lines) + "\n", encoding="utf-8")
  out = tmp_path / "report.json"

  assert main(["--db", str(db), "import", str(src)]) == 0
  assert main(["--db", str(db), "events", str(events)]) == 0
  assert main(["--db", str(db), "report", "--json", str(out)]) == 0

  assert "Rekordy w bazie" in capsys.readouterr().out
  assert json.loads(out.read_text(encoding="utf-8"))["events"]["total"] == len(stream.lines)


def test_cli_without_database_explains_what_to_do(tmp_path, capsys):
  assert main(["--db", str(tmp_path / "brak.db"), "report"]) == 2
  assert "krok-db import" in capsys.readouterr().err


def test_get_data_and_validate_read_from_database(tmp_path, engine, monkeypatch):
  monkeypatch.setenv("KROK_DB", str(tmp_path / "krok.db"))
  ds, _ = import_dir(generated(tmp_path, "mini"), engine)

  buildings = load("buildings")
  groups = load("student-groups")
  workers = load("workers")

  assert sum(len(b["rooms"]) for b in buildings) == len(ds.rooms)
  assert {g["name"] for g in groups} == {c.group_id for c in ds.classes}
  assert len(workers) == len(ds.lecturers)

  # validate_plan działa na tym samym widoku: sala innego typu niż kurs to błąd, a nie wyjątek.
  group, course = groups[0]["name"], groups[0]["courses"][0]
  building = next(b for b in buildings if any(r["profession"] != course["profession"] for r in b["rooms"]))
  room = next(r for r in building["rooms"] if r["profession"] != course["profession"])
  lesson = Lesson(day="Pon", hour=8, group=group, course=course["name"], building=building["name"], room=room["number"],
                  worker=workers[0]["name"])
  errors, _ = validate([lesson])
  assert any("jest typu" in e for e in errors)


def test_legacy_names_stay_unique_when_entities_share_names(tmp_path, engine):
  ds, _ = import_dir(generated(tmp_path, "mini"), engine)
  for items in (ds.buildings, ds.lecturers, ds.subjects):
    for item in items:
      item.name = "ta sama nazwa"

  courses = [(g["name"], c["name"]) for g in legacy_view(ds, "student-groups") for c in g["courses"]]
  buildings = [b["name"] for b in legacy_view(ds, "buildings")]
  workers = [w["name"] for w in legacy_view(ds, "workers")]

  assert len(set(courses)) == len(courses) == len(ds.classes)
  assert len(set(buildings)) == len(buildings)
  assert len(set(workers)) == len(workers)


def test_legacy_view_names_every_room(tmp_path, engine):
  ds, _ = import_dir(generated(tmp_path, "mini"), engine)
  rooms = {r["number"] for b in legacy_view(ds, "buildings") for r in b["rooms"]}
  assert rooms == {r.number for r in ds.rooms}
