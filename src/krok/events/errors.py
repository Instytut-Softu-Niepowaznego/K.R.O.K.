"""Celowo błędne zdarzenia — percepcja (#9, #11) ma je wyłapać.

| kind           | co jest zepsute                                   | jak to wykryć                    |
|----------------|---------------------------------------------------|----------------------------------|
| badReference   | odwołanie do nieistniejącej sali/prowadzącego/... | porównanie z danymi              |
| missingField   | brak wymaganego pola w payload                    | schemat (parse_event)            |
| invalidValue   | from > to, liczebność ≤ 0, pusta lista zajęć      | schemat (parse_event)            |
| unknownType    | literówka w `type`                                | schemat (parse_event)            |
| duplicate      | to samo zdarzenie (to samo `id`) drugi raz        | powtórzone `id`                  |
| outOfOrder     | `timestamp` wcześniejszy niż poprzedniego zdarzenia | najpóźniejszy dotąd timestamp  |
| malformedJson  | ucięta linia                                      | json.loads                       |
"""

import copy
import datetime as dt
import json
import random

from krok.generator.settings import EventSpec

OPTIONAL = {"reason", "fromTime", "toTime", "label", "scope"}


def to_line(event: dict) -> str:
  return json.dumps(event, ensure_ascii=False)


def _id_paths(obj, path=()):
  """Ścieżki do pól-odwołań (`...Id`) w payload, bez własnych `id` encji."""
  if isinstance(obj, dict):
    for k, v in obj.items():
      if k.endswith("Id") and isinstance(v, str):
        yield (*path, k)
      else:
        yield from _id_paths(v, (*path, k))
  elif isinstance(obj, list):
    for i, v in enumerate(obj):
      yield from _id_paths(v, (*path, i))


def _get(obj, path):
  for p in path:
    obj = obj[p]
  return obj


def _set(obj, path, value):
  _get(obj, path[:-1])[path[-1]] = value


def _dotted(path) -> str:
  return ".".join(str(p) for p in ("payload", *path))


def _bad_reference(e, rng):
  paths = list(_id_paths(e["payload"]))
  if not paths:
    return None
  path = rng.choice(paths)
  before = _get(e["payload"], path)
  _set(e["payload"], path, before + "-X")
  return _dotted(path), before, before + "-X"


def _missing_field(e, rng):
  keys = [k for k, v in e["payload"].items() if k not in OPTIONAL and v is not None]
  k = rng.choice(keys)
  return _dotted([k]), e["payload"].pop(k), None


def _invalid_value(e, rng):
  p = e["payload"]
  if "from" in p:
    before = p["to"]
    p["to"] = (dt.date.fromisoformat(p["from"]) - dt.timedelta(days=rng.randint(1, 5))).isoformat()
    return "payload.to", before, p["to"]
  if "size" in p:
    before, p["size"] = p["size"], rng.choice([0, -p["size"]])
    return "payload.size", before, p["size"]
  if "availability" in p and p["availability"]:
    a = p["availability"][0]
    a["from"], a["to"] = a["to"], a["from"]
    return "payload.availability.0", a["to"], a["from"]
  if "group" in p:
    before, p["group"]["size"] = p["group"]["size"], 0
    return "payload.group.size", before, 0
  if "subject" in p:
    before, p["classes"] = len(p["classes"]), []
    return "payload.classes", before, 0
  if "exception" in p:
    x = p["exception"]
    before = x["to"]
    x["to"] = (dt.date.fromisoformat(x["from"]) - dt.timedelta(days=1)).isoformat()
    return "payload.exception.to", before, x["to"]
  return None


def _unknown_type(e, rng):
  before = e["type"]
  i = rng.randrange(1, len(before) - 1)
  e["type"] = before[:i] + before[i + 1:]
  return "type", before, e["type"]


MUTATORS = {"badReference": _bad_reference, "missingField": _missing_field, "invalidValue": _invalid_value,
            "unknownType": _unknown_type}
ERROR_KINDS = [*MUTATORS, "duplicate", "outOfOrder", "malformedJson"]


def inject_errors(events: list[dict], spec: EventSpec, rng: random.Random) -> tuple[list[str], list[dict]]:
  kinds, weights = zip(*spec.error_weights.items())
  out: list[tuple[dict | str, dict | None]] = []  # (zdarzenie albo surowa linia, wpis manifestu)
  for e in events:
    if rng.random() >= spec.error_rate:
      out.append((e, None))
      continue
    kind = rng.choices(kinds, weights)[0]
    bad = copy.deepcopy(e)
    entry = {"eventId": e["id"], "kind": kind}
    if kind in MUTATORS:
      change = MUTATORS[kind](bad, rng)
      if change is None:
        out.append((e, None))
        continue
      entry |= dict(zip(("field", "before", "after"), change))
      out.append((bad, entry))
    elif kind == "duplicate":
      out.append((e, None))
      out.append((bad, entry | {"field": "id", "before": e["id"], "after": e["id"]}))
    elif kind == "outOfOrder":
      # Cofamy względem najpóźniejszego poprawnego zdarzenia: zepsute linie percepcja i tak odrzuci,
      # więc cofnięcie tylko względem nich byłoby niewykrywalne.
      clean = [dt.datetime.fromisoformat(x["timestamp"]) for x, prev_entry in out if prev_entry is None]
      if not clean:
        out.append((e, None))
        continue
      earlier = max(clean) - dt.timedelta(days=rng.randint(1, 5))
      bad["timestamp"] = earlier.isoformat()
      out.append((bad, entry | {"field": "timestamp", "before": e["timestamp"], "after": bad["timestamp"]}))
    elif kind == "malformedJson":
      line = to_line(e)
      out.append((line[: rng.randint(10, len(line) - 2)], entry | {"field": None, "before": None, "after": None}))
    else:
      raise ValueError(f"events.error_weights: nieznany rodzaj błędu {kind}")

  lines, manifest = [], []
  for i, (item, entry) in enumerate(out, start=1):
    lines.append(item if isinstance(item, str) else to_line(item))
    if entry:
      manifest.append({"line": i, **entry})
  return lines, manifest
