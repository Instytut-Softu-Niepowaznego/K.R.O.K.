import json
from pathlib import Path

import pytest
from sqlalchemy import select

from krok.db import ImportFailed, apply_events, build_report, connect, import_dir, read_dataset, read_issues
from krok.db.cli import main
from krok.db.clean import clean_records
from krok.db.legacy import legacy_view
from krok.db.schema import event_log
from krok.events import generate_events
from krok.generator import generate, load_preset, write
from krok.harness.plan import Lesson, validate
from krok.harness.tools.get_data import load
from krok.model import Group, Lecturer, reference_errors

ACCEPTED_NOISE_MISSES = {"badReference"}  # literówka może trafić w inny istniejący id


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

  flagged = {(i.source, i.record_id) for i in issues}
  known = {f"{name}.json": {x.id for x in getattr(ds, name)} for name in ("groups", "rooms", "lecturers", "subjects", "classes", "buildings")}
  for change in manifest:
    # Dla duplikatu z nowym id problem jest zapisany przy kopii.
    record = change["after"] if change["kind"] == "nearDuplicate" else change["id"]
    if (change["file"], record) in flagged:
      continue
    assert change["kind"] in ACCEPTED_NOISE_MISSES and change["after"] in {i for ids in known.values() for i in ids}, change
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


def test_legacy_view_names_every_room(tmp_path, engine):
  ds, _ = import_dir(generated(tmp_path, "mini"), engine)
  rooms = {r["number"] for b in legacy_view(ds, "buildings") for r in b["rooms"]}
  assert rooms == {r.number for r in ds.rooms}
