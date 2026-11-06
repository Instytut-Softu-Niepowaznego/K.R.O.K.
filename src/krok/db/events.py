"""Stosowanie strumienia zdarzeń (#8) do bazy. Każda linia trafia do dziennika `events` ze statusem.

Zdarzenie zmienia stan bazy od razu, także gdy obowiązuje od późniejszej daty: model trzyma jeden stan, bez historii.
Data obowiązywania zostaje w dzienniku (`raw`). Blokady sal i nieobecności to `resourceBlock` w kalendarzu.
"""

import datetime as dt
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.engine import Connection

from krok.events import replay
from krok.events.stream import StreamItem
from krok.model import Calendar, Class, Config, EVENT_TYPES, ResourceBlock, Scope

from .schema import TABLES, event_log, read_document, write_document


class _Rejected(Exception):
  def __init__(self, kind: str, message: str):
    super().__init__(message)
    self.kind = kind


def _exists(conn: Connection, table: str, rid: str) -> bool:
  return conn.execute(select(TABLES[table].c.id).where(TABLES[table].c.id == rid)).first() is not None


def _need(conn: Connection, table: str, rid: str, new: Iterable[str] = ()) -> None:
  if rid not in new and not _exists(conn, table, rid):
    raise _Rejected("badReference", f"nieznane {table}/{rid}")


def _free(conn: Connection, table: str, rid: str) -> None:
  if _exists(conn, table, rid):
    raise _Rejected("invalidValue", f"{table}/{rid} już istnieje")


def _check_classes(conn: Connection, classes: list[Class], *, groups=(), subjects=()) -> None:
  room_types = Config.model_validate(read_document(conn, "config")).room_types
  seen = set()
  for c in classes:
    _free(conn, "classes", c.id)
    if c.id in seen:
      raise _Rejected("invalidValue", f"powtórzone zajęcia {c.id}")
    seen.add(c.id)
    _need(conn, "subjects", c.subject_id, subjects)
    _need(conn, "groups", c.group_id, groups)
    if c.lecturer_id:
      _need(conn, "lecturers", c.lecturer_id)
    if c.room_type not in room_types:
      raise _Rejected("badReference", f"nieznany typ sali {c.room_type}")


def _insert_classes(conn: Connection, classes: list[Class]) -> None:
  if classes:
    conn.execute(TABLES["classes"].insert(), [c.model_dump(mode="json") for c in classes])


def _add_exception(conn: Connection, exception) -> None:
  calendar = Calendar.model_validate(read_document(conn, "calendar"))
  if any(x.id == exception.id for x in calendar.exceptions):
    raise _Rejected("invalidValue", f"wyjątek {exception.id} już istnieje")
  calendar.exceptions.append(exception)
  write_document(conn, "calendar", calendar.model_dump(mode="json"))


def _block(event, scope: Scope) -> ResourceBlock:
  p = event.payload
  return ResourceBlock(id=event.id, type="resourceBlock", from_=p.from_, to=p.to, from_time=getattr(p, "from_time", None),
                       to_time=getattr(p, "to_time", None), scope=scope, label=p.reason)


def _room_unavailable(conn, e):
  _need(conn, "rooms", e.payload.room_id)
  _add_exception(conn, _block(e, Scope(room_ids=[e.payload.room_id])))


def _lecturer_absent(conn, e):
  _need(conn, "lecturers", e.payload.lecturer_id)
  _add_exception(conn, _block(e, Scope(lecturer_ids=[e.payload.lecturer_id])))


def _availability_changed(conn, e):
  p = e.payload
  _need(conn, "lecturers", p.lecturer_id)
  t = TABLES["lecturers"]
  conn.execute(t.update().where(t.c.id == p.lecturer_id).values(availability=[a.model_dump(mode="json") for a in p.availability]))


def _group_size_changed(conn, e):
  p = e.payload
  _need(conn, "groups", p.group_id)
  t = TABLES["groups"]
  conn.execute(t.update().where(t.c.id == p.group_id).values(size=p.size))


def _group_added(conn, e):
  group, classes = e.payload.group, e.payload.classes
  _free(conn, "groups", group.id)
  _need(conn, "programs", group.program_id)
  if group.parent_id:
    _need(conn, "groups", group.parent_id)
  _check_classes(conn, classes, groups={group.id})
  conn.execute(TABLES["groups"].insert(), [group.model_dump(mode="json")])
  _insert_classes(conn, classes)


def _subject_added(conn, e):
  subject, classes = e.payload.subject, e.payload.classes
  _free(conn, "subjects", subject.id)
  for pool in (subject.lecturer_pools or {}).values():
    for lecturer in pool:
      _need(conn, "lecturers", lecturer)
  _check_classes(conn, classes, subjects={subject.id})
  conn.execute(TABLES["subjects"].insert(), [subject.model_dump(mode="json")])
  _insert_classes(conn, classes)


def _exception_added(conn, e):
  exception = e.payload.exception
  scope = exception.scope
  if scope:
    for table, ids in [("faculties", scope.faculty_ids), ("groups", scope.group_ids), ("rooms", scope.room_ids),
                       ("lecturers", scope.lecturer_ids)]:
      for rid in ids or []:
        _need(conn, table, rid)
  _add_exception(conn, exception)


HANDLERS = {"roomUnavailable": _room_unavailable, "lecturerAbsent": _lecturer_absent, "lecturerAvailabilityChanged": _availability_changed,
            "groupSizeChanged": _group_size_changed, "groupAdded": _group_added, "subjectAdded": _subject_added,
            "calendarExceptionAdded": _exception_added}


def _schema_error_kind(item: StreamItem) -> str:
  if item.data is None:
    return "malformedJson"
  if item.data.get("type") not in EVENT_TYPES:
    return "unknownType"
  return "missingField" if "Field required" in item.error else "invalidValue"


def _text(value) -> str | None:
  return value if isinstance(value, str) else None


def _log(conn: Connection, item: StreamItem, status: str, kind: str | None = None, message: str | None = None) -> None:
  data = item.data or {}
  conn.execute(event_log.insert().values(line=item.line, eventId=_text(data.get("id")), timestamp=_text(data.get("timestamp")),
                                         type=_text(data.get("type")), status=status, kind=kind, message=message, raw=item.text))


def _latest_applied(conn: Connection) -> dt.datetime | None:
  stamp = conn.execute(select(func.max(event_log.c.timestamp)).where(event_log.c.status == "applied")).scalar()
  return dt.datetime.fromisoformat(stamp) if stamp else None


def apply_events(engine: Engine, source: str | Path | Iterable[str]) -> Counter:
  """Zwraca liczbę linii na status. Ponowne odtworzenie tego samego pliku niczego nie zmienia w danych."""
  stats: Counter = Counter()
  with engine.begin() as conn:
    seen = set(conn.execute(select(event_log.c.eventId).where(event_log.c.status == "applied")).scalars())
    latest = _latest_applied(conn)
    for item in replay(source):
      event = item.event
      if event is None:
        _log(conn, item, "rejected", _schema_error_kind(item), item.error)
        stats["rejected"] += 1
        continue
      if event.id in seen:
        _log(conn, item, "duplicate", "duplicate", f"zdarzenie {event.id} już zastosowane")
        stats["duplicate"] += 1
        continue
      try:
        with conn.begin_nested():
          HANDLERS[event.type](conn, event)
      except _Rejected as e:
        _log(conn, item, "rejected", e.kind, str(e))
        stats["rejected"] += 1
        continue
      # Spóźniona informacja nadal jest prawdziwa, więc ją stosujemy i tylko odnotowujemy kolejność.
      late = latest is not None and event.timestamp < latest
      _log(conn, item, "applied", "outOfOrder" if late else None, f"timestamp przed {latest}" if late else None)
      stats["applied"] += 1
      seen.add(event.id)
      latest = event.timestamp if latest is None else max(latest, event.timestamp)
  return stats
