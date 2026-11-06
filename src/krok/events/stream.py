"""Odtwarzanie strumienia zdarzeń jako iterator Pythona, z przyspieszeniem czasu.

    for item in replay("data/generated/kierunek/events.jsonl", speed=86400):  # 1 dzień symulacji = 1 s
        if item.event:   # poprawny schemat
            ...
        else:            # item.error: ucięty JSON, brak pola, zła wartość, nieznany typ
            ...

Iterator nie odrzuca błędnych linii, tylko je oznacza: decyzję, co z nimi zrobić, podejmuje percepcja (#9).
Odwołań do danych (czy sala istnieje) ani duplikatów i kolejności nie sprawdza, bo to też zadanie percepcji.
"""

import datetime as dt
import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from krok.model import Event, parse_event


@dataclass
class StreamItem:
  line: int
  text: str
  data: dict | None  # None = linia nie jest poprawnym obiektem JSON
  event: Event | None  # None = schemat się nie zgadza
  error: str | None

  @property
  def timestamp(self) -> dt.datetime | None:
    if self.event:
      return self.event.timestamp
    try:
      return dt.datetime.fromisoformat(self.data["timestamp"])
    except (TypeError, KeyError, ValueError):
      return None


def parse_line(line_no: int, text: str) -> StreamItem:
  try:
    data = json.loads(text)
  except json.JSONDecodeError as e:
    return StreamItem(line_no, text, None, None, f"niepoprawny JSON: {e.msg}")
  if not isinstance(data, dict):
    return StreamItem(line_no, text, None, None, "linia nie jest obiektem JSON")
  try:
    return StreamItem(line_no, text, data, parse_event(data), None)
  except ValidationError as e:
    first = e.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "type"
    return StreamItem(line_no, text, data, None, f"{where}: {first['msg']}")


def _lines(source: str | Path | Iterable[str]) -> Iterator[tuple[int, str]]:
  if isinstance(source, (str, Path)):
    with open(source, encoding="utf-8") as f:
      yield from ((i, line.rstrip("\n")) for i, line in enumerate(f, start=1) if line.strip())
  else:
    yield from ((i, line.rstrip("\n")) for i, line in enumerate(source, start=1) if line.strip())


def replay(source: str | Path | Iterable[str], speed: float | None = None, *, start: dt.datetime | None = None,
           until: dt.datetime | None = None, sleep: Callable[[float], None] = time.sleep) -> Iterator[StreamItem]:
  """Zwraca kolejne linie strumienia.

  speed: sekundy symulacji na sekundę rzeczywistą (None = bez czekania). Czekamy tylko, gdy czas idzie do przodu,
  więc zdarzenie z cofniętym `timestamp` przychodzi od razu po poprzednim.
  start / until: pomija zdarzenia z czasem przed `start` albo po `until`. Linie bez czytelnego czasu zawsze przechodzą.
  """
  last: dt.datetime | None = None
  for line_no, text in _lines(source):
    item = parse_line(line_no, text)
    ts = item.timestamp
    if ts is not None and ((start and ts < start) or (until and ts > until)):
      continue
    if speed and ts is not None:
      if last is not None and ts > last:
        sleep((ts - last).total_seconds() / speed)
      last = ts if last is None else max(last, ts)
    yield item
