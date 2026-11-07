"""Ręczne dane bazowe WJ (#6): budynki, układ sal i budynki macierzyste kompetencji."""

import json
import math
import re
from collections import Counter
from pathlib import Path

from krok.generator import load_preset
from krok.generator.settings import load_catalog
from krok.model import Building, Config, Room

BASE = Path(__file__).parent.parent / "src/krok/data/wj"
CAMPUS = (50.682, 17.943)  # ul. Prószkowska 76
BUILDINGS_WITH_ROOMS = ["P1", "P2", "P3", "P5", "P6"]


def read(name: str):
  return json.loads((BASE / name).read_text(encoding="utf-8"))


def test_base_data_matches_campus():
  """P1–P6 przy adresie kampusu; 15 sal na kondygnację budynków z salami; wszystkie typy sal z konfiguracji."""
  buildings = [Building.model_validate(b) for b in read("buildings.json")]
  assert [b.id for b in buildings] == ["P1", "P2", "P3", "P4", "P5", "P6"]
  for b in buildings:
    dy = (b.lat - CAMPUS[0]) * 111.2
    dx = (b.lon - CAMPUS[1]) * 111.2 * math.cos(math.radians(CAMPUS[0]))
    assert math.hypot(dx, dy) < 1, b.id

  rooms = [Room.model_validate(r) for r in read("rooms.json")]
  assert len({r.id for r in rooms}) == len(rooms) == 300
  assert all(re.fullmatch(r"P[1-6]-[0-3](0[1-9]|1[0-5])", r.id) for r in rooms)
  assert Counter((r.building_id, r.number[0]) for r in rooms) == {(b, f): 15 for b in BUILDINGS_WITH_ROOMS for f in "0123"}
  assert {r.type for r in rooms} == set(Config.model_validate(read("config.json")).room_types)
  assert {r.type for r in rooms if r.building_id == "P6"} == {"classroom"}


def test_every_competence_has_home_building_with_rooms():
  """Cały katalog, nie tylko kompetencje wylosowane przez generator. P4 nie ma sal."""
  allowed = {b["id"] for b in read("buildings.json")} - {"P4"}
  for competence in load_catalog(load_preset("wydzial").catalog).competence:
    assert competence.home_buildings, competence.id
    assert set(competence.home_buildings) <= allowed, competence.id
