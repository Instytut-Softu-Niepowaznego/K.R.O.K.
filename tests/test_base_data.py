"""Ręczne dane bazowe WJ (#6): budynki i układ sal."""

import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from krok.generator import generate, load_preset
from krok.generator.settings import load_catalog
from krok.model import Building, Config, Room

BASE = Path(__file__).parent.parent / "src/krok/data/wj"
CAMPUS = (50.682, 17.943)  # ul. Prószkowska 76
BUILDINGS_WITH_ROOMS = ["P1", "P2", "P3", "P5", "P6"]


def read(name: str):
  """Wczytuje plik JSON z danych bazowych WJ."""
  return json.loads((BASE / name).read_text(encoding="utf-8"))


def km_from_campus(b: Building) -> float:
  """Przybliżona odległość budynku od adresu kampusu w kilometrach."""
  dy = (b.lat - CAMPUS[0]) * 111.2
  dx = (b.lon - CAMPUS[1]) * 111.2 * math.cos(math.radians(CAMPUS[0]))
  return math.hypot(dx, dy)


@pytest.fixture(scope="module")
def rooms() -> list[Room]:
  """Sale z rooms.json po walidacji modelem."""
  return [Room.model_validate(r) for r in read("rooms.json")]


def test_buildings_are_p1_to_p6_on_campus():
  """Są budynki P1–P6 i każdy leży blisko adresu kampusu."""
  buildings = [Building.model_validate(b) for b in read("buildings.json")]
  assert [b.id for b in buildings] == ["P1", "P2", "P3", "P4", "P5", "P6"]
  assert all(km_from_campus(b) < 1 for b in buildings)


def test_room_layout(rooms):
  """Numery sal są unikalne, a każda kondygnacja budynku z salami ma 15 sal."""
  assert len({r.id for r in rooms}) == len(rooms) == 300
  assert all(re.fullmatch(r"P[1-6]-[0-3](0[1-9]|1[0-5])", r.id) for r in rooms)
  per_floor = Counter((r.building_id, r.number[0]) for r in rooms)
  assert per_floor == {(b, f): 15 for b in BUILDINGS_WITH_ROOMS for f in "0123"}


def test_room_types_and_capacity(rooms):
  """Występują wszystkie typy sal z konfiguracji, a P6 ma same sale `classroom`."""
  types = set(Config.model_validate(read("config.json")).room_types)
  assert {r.type for r in rooms} == types
  assert {r.type for r in rooms if r.building_id == "P6"} == {"classroom"}
  assert "capacity" in read("rooms.json")[0]


def test_room_id_must_join_building_and_number():
  """Model przyjmuje `id` w formacie `budynek-numer` i odrzuca stary format."""
  Room(id="P3-213", building_id="P3", number="213", type="lab-it", capacity=16, faculty_ids=["WJ"])
  with pytest.raises(ValidationError):
    Room(id="P3/213", building_id="P3", number="213", type="lab-it", capacity=16, faculty_ids=["WJ"])


def test_every_competence_has_home_building_with_rooms():
  """Sprawdza cały katalog, a nie tylko kompetencje wylosowane przez generator.

  Pusta lista też jest błędem: generator wybrałby wtedy dowolny budynek, także P4.
  """
  allowed = {b["id"] for b in read("buildings.json")} - {"P4"}
  for competence in load_catalog(load_preset("wydzial").catalog).competence:
    assert competence.home_buildings, competence.id
    assert set(competence.home_buildings) <= allowed, competence.id


@pytest.mark.parametrize("preset", ["mini", "kierunek", "wydzial", "edge-cases"])
def test_generator_accepts_base_data(preset):
  """Generator przyjmuje dane bazowe i nie daje nikomu budynku macierzystego P4."""
  ds = generate(load_preset(preset)).dataset
  assert len(ds.rooms) == 300
  assert "P4" not in {lec.home_building_id for lec in ds.lecturers}
