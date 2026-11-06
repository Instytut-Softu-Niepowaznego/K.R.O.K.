"""Schemat SQLite wyprowadzony z modeli pydantic, żeby nie było drugiej wersji formatu.

Kolumny encji nazywają się jak pola JSON (camelCase), więc wiersz to wynik model_dump, a odczyt to model_validate.
Pola zagnieżdżone (dostępność, pule prowadzących) są w kolumnach JSON. Config i kalendarz to dokumenty w `documents`.
"""

import os
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin

from sqlalchemy import JSON, Column, Engine, Float, ForeignKey, Integer, MetaData, String, Table, Text, URL, create_engine, event, select
from sqlalchemy.engine import Connection

from krok.model import Building, Calendar, Class, Config, Dataset, Faculty, Group, Lecturer, Program, Room, StudyField, Subject
from krok.model.base import Model

from .clean import Issue

DEFAULT_DB = Path("data/krok.db")

ENTITIES: dict[str, type[Model]] = {
  "faculties": Faculty, "fields": StudyField, "programs": Program, "groups": Group, "buildings": Building, "rooms": Room,
  "lecturers": Lecturer, "subjects": Subject, "classes": Class,
}
FOREIGN_KEYS = {
  ("fields", "facultyId"): "faculties", ("programs", "fieldId"): "fields", ("groups", "programId"): "programs",
  ("groups", "parentId"): "groups", ("rooms", "buildingId"): "buildings", ("lecturers", "homeBuildingId"): "buildings",
  ("classes", "subjectId"): "subjects", ("classes", "groupId"): "groups", ("classes", "lecturerId"): "lecturers",
}

metadata = MetaData()


def _sql_type(annotation):
  origin = get_origin(annotation)
  if origin is Annotated:
    return _sql_type(get_args(annotation)[0])
  if origin in (Union, types.UnionType):
    args = [a for a in get_args(annotation) if a is not type(None)]
    return _sql_type(args[0]) if len(args) == 1 else JSON(none_as_null=True)
  if origin is Literal or annotation is str:
    return String
  if annotation is int:
    return Integer
  if annotation is float:
    return Float
  return JSON(none_as_null=True)


def _entity_table(name: str, model: type[Model]) -> Table:
  columns = []
  for field_name, field in model.model_fields.items():
    col = field.alias or field_name
    args = []
    if (name, col) in FOREIGN_KEYS:  # DEFERRED: rodzic grupy może przyjść po dziecku, sprawdzamy na końcu transakcji
      args.append(ForeignKey(f"{FOREIGN_KEYS[name, col]}.id", deferrable=True, initially="DEFERRED"))
    columns.append(Column(col, _sql_type(field.annotation), *args, primary_key=col == "id", nullable=not field.is_required()))
  return Table(name, metadata, *columns)


TABLES = {name: _entity_table(name, model) for name, model in ENTITIES.items()}

documents = Table("documents", metadata, Column("key", String, primary_key=True), Column("value", JSON, nullable=False))

import_issues = Table(
  "import_issues", metadata,
  Column("id", Integer, primary_key=True, autoincrement=True),
  Column("source", String, nullable=False),
  Column("recordId", String),
  Column("kind", String, nullable=False),
  Column("action", String, nullable=False),  # fixed | rejected | flagged
  Column("field", String),
  Column("message", Text, nullable=False),
)

event_log = Table(
  "events", metadata,
  Column("seq", Integer, primary_key=True, autoincrement=True),
  Column("line", Integer, nullable=False),
  Column("eventId", String),
  Column("timestamp", String),
  Column("type", String),
  Column("status", String, nullable=False),  # applied | rejected | duplicate
  Column("kind", String),  # rodzaj problemu, także przy applied (outOfOrder)
  Column("message", Text),
  Column("raw", Text, nullable=False),
)


def db_path(path: str | Path | None = None) -> Path:
  return Path(path or os.environ.get("KROK_DB") or DEFAULT_DB)


def connect(path: str | Path | None = None, *, create: bool = False) -> Engine:
  """Silnik SQLite z włączonymi kluczami obcymi. Bez create brak pliku bazy jest błędem."""
  target = db_path(path)
  if create:
    target.parent.mkdir(parents=True, exist_ok=True)
  elif not target.exists():
    raise FileNotFoundError(f"Brak bazy {target}. Najpierw uruchom: krok-db import <katalog z danymi>")
  engine = create_engine(URL.create("sqlite", database=str(target)))

  @event.listens_for(engine, "connect")
  def _foreign_keys(conn, _):
    conn.execute("PRAGMA foreign_keys=ON")

  return engine


def _rows(items: Iterable[Model]) -> list[dict]:
  return [i.model_dump(mode="json") for i in items]


def write_dataset(engine: Engine, ds: Dataset, issues: list[Issue]) -> None:
  """Zastępuje całą zawartość bazy, razem z dziennikiem zdarzeń: import to nowy punkt startu."""
  metadata.drop_all(engine)
  metadata.create_all(engine)
  with engine.begin() as conn:
    conn.execute(documents.insert(), [{"key": "config", "value": ds.config.model_dump(mode="json")},
                                      {"key": "calendar", "value": ds.calendar.model_dump(mode="json")}])
    for name, table in TABLES.items():
      if rows := _rows(getattr(ds, name)):
        conn.execute(table.insert(), rows)
    if issues:
      conn.execute(import_issues.insert(), [i.row() for i in issues])


def read_document(conn: Connection, key: str):
  return conn.execute(select(documents.c.value).where(documents.c.key == key)).scalar_one()


def write_document(conn: Connection, key: str, value) -> None:
  conn.execute(documents.update().where(documents.c.key == key).values(value=value))


def read_dataset(engine: Engine) -> Dataset:
  with engine.connect() as conn:
    data = {name: [ENTITIES[name].model_validate(dict(r)) for r in conn.execute(select(t)).mappings()] for name, t in TABLES.items()}
    return Dataset(config=Config.model_validate(read_document(conn, "config")),
                   calendar=Calendar.model_validate(read_document(conn, "calendar")), **data)


def read_issues(engine: Engine) -> list[Issue]:
  with engine.connect() as conn:
    return [Issue(r["source"], r["recordId"], r["kind"], r["action"], r["field"], r["message"])
            for r in conn.execute(select(import_issues).order_by(import_issues.c.id)).mappings()]
