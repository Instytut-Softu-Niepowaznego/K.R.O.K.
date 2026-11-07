import datetime as dt
import json

import pytest

from krok.events import generate_events, parse_line, replay
from krok.events.errors import _id_paths
from krok.generator import generate, load_preset
from krok.model import EVENT_TYPES


def known_ids(ds) -> set[str]:
  return {x.id for coll in (ds.rooms, ds.lecturers, ds.groups, ds.subjects, ds.classes, ds.programs) for x in coll}


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


def test_stream_covers_all_event_types_and_error_kinds():
  preset = load_preset("kierunek")
  preset.events.per_week, preset.events.error_rate = 12, 0.4
  stream = generate_events(generate(preset).dataset, preset, 42)
  assert set(stream.meta["types"]) == set(EVENT_TYPES)
  assert {e["kind"] for e in stream.manifest} == {"badReference", "missingField", "invalidValue", "unknownType", "duplicate",
                                                  "outOfOrder", "malformedJson"}


def assert_stream_detectable(ds, stream):
  """Poprawne linie: schemat, istniejące odwołania, nowe id bez kolizji, kolejność, daty w semestrze.
  Wpisy manifestu: każdy da się wykryć."""
  cal = ds.calendar
  base = known_ids(ds)
  known = base | {i for line in stream.lines for i in _created_ids(line)}
  by_line = {e["line"]: e for e in stream.manifest}
  seen, latest, created_so_far = set(), None, set()
  for i, text in enumerate(stream.lines, start=1):
    item, entry = parse_line(i, text), by_line.get(i)
    if entry is None:
      assert item.event, (i, item.error)
      assert item.event.id not in seen and (latest is None or item.event.timestamp >= latest), i
      created = _created_ids(text)
      assert not created & (base | created_so_far), (i, created)
      created_so_far |= created
      for path in _id_paths(item.data["payload"]):
        value = item.data["payload"]
        for p in path:
          value = value[p]
        assert value in known, (i, path, value)
      for key in ("from", "to", "effectiveFrom"):
        if key in item.data["payload"]:
          assert cal.semester_start <= dt.date.fromisoformat(item.data["payload"][key]) <= cal.semester_end, (i, key)
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
def test_valid_events_are_consistent_and_errors_detectable(name):
  preset = load_preset(name)
  preset.events.error_rate = 0.5
  ds = generate(preset).dataset
  for seed in range(30):
    assert_stream_detectable(ds, generate_events(ds, preset, seed))


def _line(i: int, ts: str) -> str:
  return json.dumps({"id": f"E-{i}", "timestamp": ts, "type": "lecturerAbsent",
                     "payload": {"lecturerId": "P-0001", "from": "2026-10-10", "to": "2026-10-11"}})


def test_replay_paces_filters_and_survives_bad_lines():
  lines = [_line(1, "2026-10-01T10:00:00"), _line(2, "2026-10-02T10:00:00"), "{ucięte", _line(3, "2026-10-01T12:00:00"),
           _line(4, "2026-10-03T10:00:00"), _line(5, "2026-10-03T12:00:00+02:00")]
  sleeps: list[float] = []
  items = list(replay(lines, speed=86400, sleep=sleeps.append))
  assert [it.line for it in items] == [1, 2, 3, 4, 5, 6]
  assert items[2].data is None and "JSON" in items[2].error
  assert items[5].event is None  # strefa czasowa to błąd schematu
  assert sleeps == [1.0, 1.0]  # cofnięty czas (linia 4) i linia ze strefą nie czekają
  start, until = dt.datetime(2026, 10, 2), dt.datetime(2026, 10, 2, 23)
  assert [it.line for it in replay(lines, start=start, until=until)] == [2, 3, 6]  # linia bez czasu zawsze przechodzi
