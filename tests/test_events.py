import datetime as dt
import json

import pytest
from pydantic import ValidationError

from krok.events import generate_events, parse_line, replay
from krok.events.cli import main
from krok.events.errors import _id_paths
from krok.generator import generate, load_preset
from krok.model import EVENT_TYPES, parse_event

SCHEMA_ERRORS = {"missingField", "invalidValue", "unknownType"}


@pytest.fixture(scope="module")
def world():
  preset = load_preset("kierunek")
  preset.events.per_week = 12
  preset.events.error_rate = 0.4
  ds = generate(preset).dataset
  return preset, ds, generate_events(ds, preset, 42)


def known_ids(ds) -> set[str]:
  return {x.id for coll in (ds.rooms, ds.lecturers, ds.groups, ds.subjects, ds.classes, ds.programs) for x in coll}


def test_same_seed_same_stream(world):
  preset, ds, stream = world
  assert generate_events(ds, preset, 42).lines == stream.lines
  assert generate_events(ds, preset, 43).lines != stream.lines


def test_all_types_and_error_kinds_present(world):
  _, _, stream = world
  assert set(stream.meta["types"]) == set(EVENT_TYPES)
  assert {e["kind"] for e in stream.manifest} == {"badReference", "missingField", "invalidValue", "unknownType", "duplicate",
                                                  "outOfOrder", "malformedJson"}


def test_valid_events_parse_and_point_to_existing_data(world):
  _, ds, stream = world
  bad_lines = {e["line"] for e in stream.manifest}
  known = known_ids(ds)
  competences = {c for lec in ds.lecturers for c in lec.competences}
  new_ids: set[str] = set()
  for i, text in enumerate(stream.lines, start=1):
    if i in bad_lines:
      continue
    item = parse_line(i, text)
    assert item.event is not None, (i, item.error)
    e = item.event
    created = set()
    if e.type == "groupAdded":
      created = {e.payload.group.id, *(c.id for c in e.payload.classes)}
    elif e.type == "subjectAdded":
      created = {e.payload.subject.id, *(c.id for c in e.payload.classes)}
      assert e.payload.subject.competence in competences
    assert not created & (known | new_ids), f"{e.id}: id już istnieje"
    new_ids |= created
    for path in _id_paths(item.data["payload"]):
      value = item.data["payload"]
      for p in path:
        value = value[p]
      assert value in known | created, (e.id, path, value)


def test_valid_events_are_ordered_and_inside_semester(world):
  _, ds, stream = world
  cal = ds.calendar
  bad_lines = {e["line"] for e in stream.manifest}
  items = [parse_line(i, t) for i, t in enumerate(stream.lines, start=1) if i not in bad_lines]
  stamps = [it.event.timestamp for it in items]
  assert stamps == sorted(stamps)
  assert stamps[0].date() >= cal.semester_start - dt.timedelta(days=7) and stamps[-1].date() <= cal.semester_end
  for it in items:
    p = it.data["payload"]
    for key in ("from", "to", "effectiveFrom"):
      if key in p:
        assert cal.semester_start <= dt.date.fromisoformat(p[key]) <= cal.semester_end, (it.event.id, key)


def test_every_injected_error_is_detectable(world):
  """Każdy wpis manifestu da się wykryć: schematem, danymi, powtórzonym id albo kolejnością."""
  _, ds, stream = world
  known = known_ids(ds) | {e for line in stream.lines for e in _created_ids(line)}
  seen_ids, latest = set(), None
  by_line = {e["line"]: e for e in stream.manifest}
  for i, text in enumerate(stream.lines, start=1):
    item = parse_line(i, text)
    entry = by_line.get(i)
    if entry:
      kind = entry["kind"]
      if kind == "malformedJson":
        assert item.data is None
      elif kind in SCHEMA_ERRORS:
        assert item.event is None and item.error, entry
      elif kind == "badReference":
        assert item.event is not None and entry["after"] not in known
      elif kind == "duplicate":
        assert item.event.id in seen_ids
      elif kind == "outOfOrder":
        assert item.event.timestamp < latest
    if item.event:
      seen_ids.add(item.event.id)
      latest = item.event.timestamp if latest is None else max(latest, item.event.timestamp)


def _created_ids(line: str) -> set[str]:
  try:
    p = json.loads(line)["payload"]
  except (json.JSONDecodeError, KeyError):
    return set()
  out = set()
  for key in ("group", "subject"):
    if isinstance(p.get(key), dict) and "id" in p[key]:
      out.add(p[key]["id"])
  out |= {c["id"] for c in p.get("classes", []) if isinstance(c, dict) and "id" in c}
  return out


def test_no_errors_option(world):
  preset, ds, _ = world
  stream = generate_events(ds, preset, 42, errors=False)
  assert stream.manifest == [] and all(parse_line(i, t).event for i, t in enumerate(stream.lines, start=1))


@pytest.mark.parametrize("payload", [
  {"type": "roomUnavailable", "payload": {"roomId": "P1-101", "from": "2026-10-10", "to": "2026-10-01"}},
  {"type": "roomUnavailable", "payload": {"roomId": "P1-101", "from": "2026-10-10", "to": "2026-10-11", "fromTime": "10:00"}},
  {"type": "groupSizeChanged", "payload": {"groupId": "g", "size": 0, "effectiveFrom": "2026-10-10"}},
  {"type": "groupAdded", "payload": {"group": {"id": "g", "programId": "p", "parentId": "x", "kind": "lab", "size": 5}}},
  {"type": "subjectAdded", "payload": {"subject": {"id": "s", "name": "S", "competence": "c"}, "classes": []}},
  {"type": "roomBroken", "payload": {}},
])
def test_schema_rejects_bad_events(payload):
  with pytest.raises(ValidationError):
    parse_event({"id": "E-1", "timestamp": "2026-10-01T10:00:00", **payload})


def _line(i: int, ts: str) -> str:
  return json.dumps({"id": f"E-{i}", "timestamp": ts, "type": "lecturerAbsent",
                     "payload": {"lecturerId": "P-0001", "from": "2026-10-10", "to": "2026-10-11"}})


def test_replay_speed_filters_and_bad_lines():
  lines = [_line(1, "2026-10-01T10:00:00"), _line(2, "2026-10-02T10:00:00"), "{ucięte", _line(3, "2026-10-01T12:00:00"),
           _line(4, "2026-10-03T10:00:00")]
  sleeps: list[float] = []
  items = list(replay(lines, speed=86400, sleep=sleeps.append))
  assert [it.line for it in items] == [1, 2, 3, 4, 5]
  assert items[2].data is None and "JSON" in items[2].error
  assert sleeps == [1.0, 1.0]  # cofnięty czas (linia 4) nie czeka, a kolejny krok liczy się od najpóźniejszego
  assert list(replay(lines, sleep=sleeps.append)) and sleeps == [1.0, 1.0]  # bez speed nie czeka
  start, until = dt.datetime(2026, 10, 2), dt.datetime(2026, 10, 2, 23)
  assert [it.line for it in replay(lines, start=start, until=until)] == [2, 3]  # linia bez czasu zawsze przechodzi


def test_cli_generate_and_replay(tmp_path, capsys):
  assert main(["generate", "--preset", "mini", "--seed", "3", "--out", str(tmp_path), "--error-rate", "0.5"]) == 0
  assert (tmp_path / "classes.json").exists() and (tmp_path / "events.jsonl").exists()
  manifest = json.loads((tmp_path / "_events.json").read_text())
  assert manifest["seed"] == 3 and manifest["errors"] == len(manifest["errorsList"]) > 0
  assert main(["generate", "--preset", "mini", "--data", str(tmp_path), "--error-rate", "0.5", "-q"]) == 0  # seed z _meta.json
  assert (tmp_path / "_events.json").read_text() == json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
  capsys.readouterr()
  assert main(["replay", str(tmp_path / "events.jsonl"), "--errors-only"]) == 0
  assert "✗" in capsys.readouterr().out
  assert main(["replay", str(tmp_path / "nie-ma.jsonl")]) == 2


def test_unknown_config_keys_fail_fast():
  preset = load_preset("mini")
  ds = generate(preset).dataset
  preset.events.weights = {"period": 1}  # nazwa metody pomocniczej, a nie typ zdarzenia
  with pytest.raises(ValueError, match="events.weights"):
    generate_events(ds, preset, 1)
  preset = load_preset("mini")
  preset.events.error_weights = {"typo": 1}
  with pytest.raises(ValueError, match="events.error_weights"):
    generate_events(ds, preset, 1)


def test_new_ids_never_collide_with_gappy_data():
  """Numeracja grup z dziurami (np. dane spoza generatora) nie prowadzi do duplikatów id."""
  preset = load_preset("mini")
  preset.events.per_week, preset.events.weights = 30, {"groupAdded": 1, "subjectAdded": 1, "calendarExceptionAdded": 1}
  ds = generate(preset).dataset
  lab = next(g for g in ds.groups if g.kind == "lab")
  ds.groups.append(lab.model_copy(update={"id": lab.id.rsplit("-L", 1)[0] + "-L6"}))  # L1–L4 i L6
  stream = generate_events(ds, preset, 5, errors=False)
  ids = known_ids(ds) | {e.id for e in ds.calendar.exceptions}
  for i, text in enumerate(stream.lines, start=1):
    created = _created_ids(text) | {e["id"] for e in [json.loads(text)["payload"].get("exception")] if e}
    assert not created & ids, created & ids
    ids |= created


def test_elective_competence_fits_cohort_mode(world):
  _, ds, stream = world
  mode = {p.id: p.study_mode for p in ds.programs}
  groups = {g.id: g for g in ds.groups}
  for text in stream.lines:
    item = parse_line(0, text)
    if item.event and item.event.type == "subjectAdded":
      weekend = mode[groups[item.event.payload.classes[0].group_id].program_id] == "part-time"
      comp = item.event.payload.subject.competence
      assert any(comp in lec.competences and any((a.day in ("So", "Nd")) == weekend for a in lec.availability)
                 for lec in ds.lecturers), item.event.id


def test_partial_room_blocks_inside_day(world):
  _, ds, stream = world
  grid = ds.config.time_grid
  for text in stream.lines:
    item = parse_line(0, text)
    if item.event and item.event.type == "roomUnavailable" and item.event.payload.from_time:
      p = item.event.payload
      assert grid.day_start <= p.from_time < p.to_time <= grid.day_end, item.event.id


def test_cli_uses_preset_from_data_and_validates_args(tmp_path, capsys):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "edge-cases", "--out", str(tmp_path), "-q"]) == 0
  assert main(["generate", "--data", str(tmp_path), "-q"]) == 0
  assert json.loads((tmp_path / "_events.json").read_text())["preset"] == "edge-cases"
  assert main(["generate", "--data", str(tmp_path / "pusty")]) == 2
  assert main(["generate", "--preset", "mini", "--out", str(tmp_path), "--error-rate", "2"]) == 2
  assert main(["generate", "--preset", "mini", "--out", str(tmp_path), "--per-week", "0"]) == 2


def assert_stream_detectable(ds, stream):
  """Poprawne linie: schemat, istniejące odwołania, kolejność. Wpisy manifestu: każdy da się wykryć."""
  known = known_ids(ds) | {i for line in stream.lines for i in _created_ids(line)}
  by_line = {e["line"]: e for e in stream.manifest}
  seen, latest = set(), None
  for i, text in enumerate(stream.lines, start=1):
    item, entry = parse_line(i, text), by_line.get(i)
    if entry is None:
      assert item.event, (i, item.error)
      assert item.event.id not in seen and (latest is None or item.event.timestamp >= latest), i
      for path in _id_paths(item.data["payload"]):
        value = item.data["payload"]
        for p in path:
          value = value[p]
        assert value in known, (i, path, value)
    else:
      kind = entry["kind"]
      detected = {
        "malformedJson": item.data is None,
        "badReference": item.event is not None and entry["after"] not in known,
        "duplicate": item.event is not None and item.event.id in seen,
        "outOfOrder": item.event is not None and latest is not None and item.event.timestamp < latest,
      }.get(kind, item.event is None)  # missingField, invalidValue, unknownType: schemat
      assert detected, entry
    if item.event:
      seen.add(item.event.id)
      latest = item.event.timestamp if latest is None else max(latest, item.event.timestamp)


@pytest.mark.parametrize("name", ["mini", "edge-cases", "kierunek"])
def test_errors_detectable_across_seeds(name):
  preset = load_preset(name)
  preset.events.error_rate = 0.5
  ds = generate(preset).dataset
  for seed in range(30):
    assert_stream_detectable(ds, generate_events(ds, preset, seed))


def test_offset_timestamp_is_schema_error():
  with pytest.raises(ValidationError):
    parse_event(json.loads(_line(1, "2026-10-01T10:00:00+02:00")))


def test_replay_survives_mixed_timezones():
  """Linia ze strefą czasową jest błędem schematu i nie przerywa odtwarzania ani filtrowania."""
  broken = json.loads(_line(9, "2026-10-01T09:00:00+02:00"))
  broken["type"] = "zly-typ"
  lines = [_line(1, "2026-10-01T10:00:00"), _line(2, "2026-10-02T10:00:00+02:00"), json.dumps(broken),
           _line(3, "2026-10-03T10:00:00")]
  sleeps: list[float] = []
  items = list(replay(lines, speed=86400, start=dt.datetime(2026, 9, 1), until=dt.datetime(2026, 12, 1), sleep=sleeps.append))
  assert [it.line for it in items] == [1, 2, 3, 4]
  assert items[1].event is None and items[1].timestamp is None and items[2].timestamp is None
  assert sleeps == [2.0]  # linie ze strefą nie wpływają na czekanie


@pytest.mark.parametrize("bound", ["start", "until"])
def test_replay_rejects_aware_bounds(bound):
  aware = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
  with pytest.raises(ValueError, match=bound):
    list(replay([_line(1, "2026-10-01T10:00:00")], **{bound: aware}))


def test_cli_generate_from_noisy_data_is_exit_2(tmp_path, capsys):
  """Dane z szumem nie przechodzą walidacji: komunikat i kod 2 zamiast tracebacku."""
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--noise", "--out", str(tmp_path), "-q"]) == 0
  assert main(["generate", "--data", str(tmp_path), "-q"]) == 2
  assert "czystych danych" in capsys.readouterr().err


def test_cli_generate_from_data_with_broken_references_is_exit_2(tmp_path, capsys):
  """Wiszący parentId przechodzi walidację pojedynczych rekordów, ale generator zdarzeń chodzi po drzewie grup."""
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0
  groups = json.loads((tmp_path / "groups.json").read_text())
  next(g for g in groups if g["parentId"])["parentId"] = "NIE-MA"
  (tmp_path / "groups.json").write_text(json.dumps(groups))
  assert main(["generate", "--data", str(tmp_path), "-q"]) == 2
  assert "spójności" in capsys.readouterr().err


@pytest.mark.parametrize("broken", ["utf8", "directory"])
def test_cli_replay_unreadable_file_is_exit_2(tmp_path, broken, capsys):
  path = tmp_path / "zdarzenia.jsonl"
  if broken == "utf8":
    path.write_bytes(b"\xff\xfe zly utf8\n")
  else:
    path.mkdir()
  assert main(["replay", str(path)]) == 2
  assert "Nie można odczytać" in capsys.readouterr().err


def test_cli_replay_closed_pipe_is_not_an_input_error(tmp_path):
  """`krok-events replay … | head -1`: zamknięty potok to nie błąd odczytu pliku, więc bez komunikatu i z kodem 0."""
  import subprocess
  import sys
  path = tmp_path / "zdarzenia.jsonl"
  path.write_text("\n".join(_line(i, "2026-10-01T10:00:00") for i in range(30_000)) + "\n")  # więcej niż bufor potoku
  proc = subprocess.Popen([sys.executable, "-m", "krok.events.cli", "replay", str(path)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
  # Same pola ASCII: przy kodowaniu potoku innym niż UTF-8 (np. cp1252) safe_output() zamienia ✓ na „?”.
  assert b"2026-10-01 10:00 E-0 lecturerAbsent" in proc.stdout.readline()
  proc.stdout.close()  # jak head -1
  err = proc.stderr.read()
  assert proc.wait() == 0
  assert b"Traceback" not in err and "Nie można odczytać".encode() not in err


def test_cli_generate_from_data_with_invalid_utf8_is_exit_2(tmp_path, capsys):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0
  (tmp_path / "groups.json").write_bytes(b"\xff\xfe")
  assert main(["generate", "--data", str(tmp_path), "-q"]) == 2
  assert "Nie można wczytać" in capsys.readouterr().err
