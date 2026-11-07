import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from krok.db import apply_events, connect, import_dir, read_dataset
from krok.db.schema import event_log
from krok.events import generate_events
from krok.generator import generate, load_preset, write
from krok.harness.plan import Lesson, validate
from krok.harness.tools.get_data import load
from krok.model import reference_errors

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
  """Porównuje pole z raportu z polem z manifestu, niezależnie od zapisu."""
  return (issue_field or "").replace("_", "").lower() == field.lower()


@pytest.fixture
def engine(tmp_path):
  """Pusta baza w katalogu testu."""
  e = connect(tmp_path / "krok.db", create=True)
  yield e
  e.dispose()


def generated(tmp_path: Path, name: str, *, noise: bool = False, seed: int | None = None, edge: bool | None = None) -> Path:
  """Generuje preset do katalogu testu i zwraca ścieżkę."""
  preset = load_preset(name)
  preset.noise.enabled = noise
  if edge is not None:
    preset.edge_cases.enabled = edge
  out = tmp_path / f"{name}-{seed}"
  write(generate(preset, seed), out)
  return out


@pytest.mark.parametrize("name", ["mini", "kierunek"])
def test_clean_data_imports_without_issues_and_roundtrips(tmp_path, engine, name):
  """Czyste dane przechodzą bez problemów i wracają z bazy bez zmian."""
  preset = load_preset(name)
  preset.noise.enabled = False
  expected = generate(preset).dataset
  src = tmp_path / "src"
  write(generate(preset), src)

  _, issues = import_dir(src, engine)

  assert issues == []
  assert read_dataset(engine) == expected


def test_edge_cases_keep_all_records(tmp_path, engine):
  """Przypadki brzegowe to poprawne dane, więc nic nie jest odrzucane."""
  src = generated(tmp_path, "edge-cases")
  ds, issues = import_dir(src, engine)
  assert [i for i in issues if i.action == "rejected"] == []
  assert reference_errors(ds) == []


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("name", ["mini", "kierunek"])
def test_noise_is_detected_and_database_stays_consistent(tmp_path, engine, name, seed):
  """Każda zmiana z `_noise.json` jest wykryta, a baza zostaje spójna."""
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


LIST_NAMES = ["groups", "buildings", "rooms", "lecturers", "subjects", "classes"]


def csv_cell(value) -> str:
  """Wartość JSON zapisana tak, jak zrobiłby to człowiek w arkuszu."""
  if value is None:
    return ""
  if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
    return ";".join(value)
  return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)


def json_to_csv(src: Path, name: str, *, encoding: str = "utf-8") -> None:
  """Zastępuje plik JSON listy równoważnym plikiem CSV."""
  records = json.loads((src / f"{name}.json").read_text(encoding="utf-8"))
  with (src / f"{name}.csv").open("w", encoding=encoding, newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows({k: csv_cell(v) for k, v in r.items()} for r in records)
  (src / f"{name}.json").unlink()


@pytest.mark.parametrize("name", ["mini", "edge-cases"])
def test_csv_files_import_like_json(tmp_path, engine, name):
  """Wszystkie pliki list w CSV dają te same dane co JSON, bez problemów w raporcie."""
  src = generated(tmp_path, name)
  expected, _ = import_dir(src, engine)
  for list_name in LIST_NAMES:
    json_to_csv(src, list_name)

  ds, issues = import_dir(src, engine)

  assert ds == expected
  assert read_dataset(engine) == expected
  assert [i for i in issues if i.action == "rejected"] == []


def stream_for(name: str, tmp_path: Path, seed: int):
  """Dane presetu i strumień zdarzeń z dużą liczbą błędów."""
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
  """Każda zepsuta linia strumienia ma właściwy rodzaj błędu."""
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
  before = read_dataset(engine)
  assert reference_errors(before) == []

  # Ponowne odtworzenie tego samego strumienia niczego nie zmienia.
  assert apply_events(engine, stream.lines)["applied"] == 0
  assert read_dataset(engine) == before


def test_events_change_state(tmp_path, engine):
  """Zdarzenia zmieniają dane w bazie."""
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


def test_failure_rolls_back_events_and_log(tmp_path, engine):
  """Awaria w trakcie strumienia cofa zastosowane już zdarzenia i ich wpisy w dzienniku."""
  import_dir(generated(tmp_path, "mini"), engine)
  before = read_dataset(engine)
  group = before.groups[0]

  def lines():
    """Poprawne zdarzenie, a po nim awaria strumienia."""
    yield json.dumps({"id": "E-1", "timestamp": "2026-10-01T10:00:00", "type": "groupSizeChanged",
                      "payload": {"groupId": group.id, "size": group.size + 1, "effectiveFrom": "2026-10-05"}})
    raise RuntimeError("przerwany strumień")

  with pytest.raises(RuntimeError, match="przerwany strumień"):
    apply_events(engine, lines())

  assert read_dataset(engine) == before
  with engine.connect() as conn:
    assert conn.execute(select(event_log)).all() == []


def test_get_data_and_validate_read_from_database(tmp_path, engine, monkeypatch):
  """`get_data` i `validate` działają na danych z bazy."""
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


