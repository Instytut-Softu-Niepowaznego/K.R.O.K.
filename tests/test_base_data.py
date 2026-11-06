"""Ręczne dane bazowe WJ (#6): budynki i układ sal."""

import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from krok.generator import generate, load_preset
from krok.model import Building, Config, Room

BASE = Path(__file__).parent.parent / "src/krok/data/wj"
CAMPUS = (50.682, 17.943)  # ul. Prószkowska 76
BUILDINGS_WITH_ROOMS = ["P1", "P2", "P3", "P5", "P6"]


def read(name: str):
  return json.loads((BASE / name).read_text(encoding="utf-8"))


def km_from_campus(b: Building) -> float:
  dy = (b.lat - CAMPUS[0]) * 111.2
  dx = (b.lon - CAMPUS[1]) * 111.2 * math.cos(math.radians(CAMPUS[0]))
  return math.hypot(dx, dy)


@pytest.fixture(scope="module")
def rooms() -> list[Room]:
  return [Room.model_validate(r) for r in read("rooms.json")]


def test_buildings_are_p1_to_p6_on_campus():
  buildings = [Building.model_validate(b) for b in read("buildings.json")]
  assert [b.id for b in buildings] == ["P1", "P2", "P3", "P4", "P5", "P6"]
  assert all(km_from_campus(b) < 1 for b in buildings)


def test_room_layout(rooms):
  assert len({r.id for r in rooms}) == len(rooms) == 300
  assert all(re.fullmatch(r"P[1-6]-[0-3](0[1-9]|1[0-5])", r.id) for r in rooms)
  per_floor = Counter((r.building_id, r.number[0]) for r in rooms)
  assert per_floor == {(b, f): 15 for b in BUILDINGS_WITH_ROOMS for f in "0123"}


def test_room_types_and_capacity(rooms):
  types = set(Config.model_validate(read("config.json")).room_types)
  assert {r.type for r in rooms} == types
  assert {r.type for r in rooms if r.building_id == "P6"} == {"classroom"}
  assert "capacity" in read("rooms.json")[0]


def test_room_id_must_join_building_and_number():
  Room(id="P3-213", building_id="P3", number="213", type="lab-it", capacity=16, faculty_ids=["WJ"])
  with pytest.raises(ValidationError):
    Room(id="P3/213", building_id="P3", number="213", type="lab-it", capacity=16, faculty_ids=["WJ"])


@pytest.mark.parametrize("preset", ["mini", "kierunek", "wydzial", "edge-cases"])
def test_generator_accepts_base_data(preset):
  ds = generate(load_preset(preset)).dataset
  assert len(ds.rooms) == 300
  assert "P4" not in {lec.home_building_id for lec in ds.lecturers}
