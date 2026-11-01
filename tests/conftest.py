import pytest

from krok.harness import agent, plan
from krok.harness.plan import Lesson

BUILDINGS = [
  {"name": "A", "rooms": [
    {"number": "1", "profession": "IT"},
    {"number": "2", "profession": "Lecture"},
  ]},
  {"name": "B", "rooms": [{"number": "3", "profession": "IT"}]},
]

GROUPS = [
  {"name": "G1", "courses": [
    {"name": "Prog", "hoursWeekly": 1, "profession": "IT", "domain": "IT"},
    {"name": "Wykład", "hoursWeekly": 1, "profession": "Lecture", "domain": "IT"},
  ]},
  {"name": "G2", "courses": [
    {"name": "Sieci", "hoursWeekly": 1, "profession": "IT", "domain": "IT"},
  ]},
]

WORKERS = [
  {"name": "Anna", "Profesion": ["IT"], "FavouriteBuilding": "A"},
  {"name": "Piotr", "Profesion": ["IT"], "FavouriteBuilding": "A"},
  {"name": "Ewa", "Profesion": ["Electronics"], "FavouriteBuilding": "B"},
]

DATA = {"buildings": BUILDINGS, "student-groups": GROUPS, "workers": WORKERS}


def lesson(**fields) -> Lesson:
  """Jedyne miejsce budujące lekcję: zmiana modelu czasu dotyka testów tutaj."""
  defaults = dict(day="Pon", hour=8, group="G1", course="Prog", building="A", room="1", worker="Anna")
  return Lesson(**(defaults | fields))


@pytest.fixture
def valid_plan() -> list[Lesson]:
  """Plan bez błędów i ostrzeżeń dla danych testowych."""
  return [
    lesson(),
    lesson(hour=9, course="Wykład", room="2"),
    lesson(day="Wt", group="G2", course="Sieci", worker="Piotr"),
  ]


@pytest.fixture(autouse=True)
def test_data(monkeypatch):
  """Testy nie zależą od danych w src/krok/data, które będą się zmieniać."""
  monkeypatch.setattr(plan, "load", DATA.__getitem__)


class _NoLLM:
  def __getattr__(self, name):
    raise AssertionError("Test próbował użyć prawdziwego klienta LLM")


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
  """Żaden test nie może przypadkiem wyjść do sieci."""
  monkeypatch.setattr(agent, "client", _NoLLM())
