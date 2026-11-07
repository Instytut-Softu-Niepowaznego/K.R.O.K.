import pytest

from conftest import lesson
from krok.harness.plan import validate

pytestmark = pytest.mark.usefixtures("test_data")


def broken(plan, index, **changes):
  """Kopia poprawnego planu z jedną zmienioną lekcją."""
  return [l.model_copy(update=changes) if i == index else l for i, l in enumerate(plan)]


def test_valid_plan_has_no_errors_or_warnings(valid_plan):
  assert validate(valid_plan) == ([], [])


@pytest.mark.parametrize("index, changes, expected", [
  pytest.param(0, {"course": "Brak"}, "grupa nie ma takiego kursu", id="kurs"),
  pytest.param(0, {"room": "99"}, "nie ma sali 99", id="sala"),
  pytest.param(0, {"worker": "Brak"}, "nie ma prowadzącego Brak", id="prowadzący"),
  pytest.param(0, {"room": "2"}, "jest typu Lecture, kurs wymaga IT", id="typ-sali"),
  pytest.param(0, {"worker": "Ewa"}, "Ewa nie uczy IT", id="kompetencja"),
  pytest.param(2, {"day": "Pon"}, "sala zajęta", id="kolizja-sali"),
  pytest.param(2, {"day": "Pon", "room": "3", "building": "B", "worker": "Anna"}, "Anna ma już zajęcia", id="kolizja-prowadzącego"),
  pytest.param(1, {"hour": 8, "worker": "Piotr"}, "grupa ma już zajęcia", id="kolizja-grupy"),
  pytest.param(None, None, "G2 / Sieci: 0h w planie, wymagane 1h", id="za-mało-godzin"),
])
def test_hard_rule_violation_is_an_error(valid_plan, index, changes, expected):
  plan = valid_plan[:-1] if index is None else broken(valid_plan, index, **changes)
  errors, _ = validate(plan)
  assert any(expected in e for e in errors), errors


def test_soft_rules_are_only_warnings():
  # Anna w Pon: A (8), A (9), B (10) podane nie po kolei. Po godzinach to 1 zmiana budynku, nie 2.
  plan = [
    lesson(hour=8),
    lesson(hour=10, group="G2", course="Sieci", building="B", room="3"),
    lesson(hour=9, course="Wykład", room="2"),
  ]
  errors, warnings = validate(plan)
  assert errors == []
  assert "Anna w Pon: 1 zmian(y) budynku" in warnings
  assert any("poza ulubionym budynkiem (A)" in w for w in warnings)
