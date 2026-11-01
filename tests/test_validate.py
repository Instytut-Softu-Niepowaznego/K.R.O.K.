import pytest

from conftest import lesson
from krok.harness.plan import validate


def broken(plan, index, **changes):
  """Kopia poprawnego planu z jedną zmienioną lekcją."""
  return [l.model_copy(update=changes) if i == index else l for i, l in enumerate(plan)]


def test_valid_plan_has_no_errors_or_warnings(valid_plan):
  assert validate(valid_plan) == ([], [])


@pytest.mark.parametrize("index, changes, expected", [
  pytest.param(0, {"course": "Brak"}, "grupa nie ma takiego kursu", id="kurs"),
  pytest.param(0, {"room": "99"}, "nie ma sali 99", id="sala"),
  pytest.param(0, {"building": "Brak"}, "nie ma sali 1 w budynku Brak", id="budynek"),
  pytest.param(0, {"worker": "Brak"}, "nie ma prowadzącego Brak", id="prowadzący"),
])
def test_unknown_reference_is_an_error(valid_plan, index, changes, expected):
  errors, _ = validate(broken(valid_plan, index, **changes))
  assert any(expected in e for e in errors), errors


def test_room_type_must_match_course(valid_plan):
  errors, _ = validate(broken(valid_plan, 0, room="2"))
  assert any("jest typu Lecture, kurs wymaga IT" in e for e in errors), errors


def test_worker_must_teach_course_domain(valid_plan):
  errors, _ = validate(broken(valid_plan, 0, worker="Ewa"))
  assert any("Ewa nie uczy IT" in e for e in errors), errors


@pytest.mark.parametrize("index, changes, expected", [
  pytest.param(2, {"day": "Pon"}, "sala zajęta", id="sala"),
  pytest.param(2, {"day": "Pon", "room": "3", "building": "B", "worker": "Anna"}, "Anna ma już zajęcia", id="prowadzący"),
  pytest.param(1, {"hour": 8, "worker": "Piotr"}, "grupa ma już zajęcia", id="grupa"),
])
def test_collision_is_an_error(valid_plan, index, changes, expected):
  errors, _ = validate(broken(valid_plan, index, **changes))
  assert any(expected in e and "koliduje z #" in e for e in errors), errors


def test_same_room_at_different_hour_is_not_a_collision(valid_plan):
  errors, _ = validate(broken(valid_plan, 2, day="Pon", hour=10))
  assert errors == []


def test_too_few_hours_is_an_error(valid_plan):
  errors, _ = validate(valid_plan[:-1])
  assert errors == ["G2 / Sieci: 0h w planie, wymagane 1h"]


def test_too_many_hours_is_an_error(valid_plan):
  errors, _ = validate(valid_plan + [lesson(day="Śr")])
  assert errors == ["G1 / Prog: 2h w planie, wymagane 1h"]


def test_building_outside_favourite_is_only_a_warning(valid_plan):
  errors, warnings = validate(broken(valid_plan, 2, building="B", room="3"))
  assert errors == []
  assert len(warnings) == 1
  assert "poza ulubionym budynkiem (A)" in warnings[0]


def test_building_switch_during_day_is_only_a_warning(valid_plan):
  errors, warnings = validate(broken(valid_plan, 0, building="B", room="3"))
  assert errors == []
  assert "Anna w Pon: 1 zmian(y) budynku" in warnings


def test_building_switch_is_counted_in_hour_order():
  # W kolejności podania A, B, A to 2 zmiany; po godzinach A, A, B to tylko 1.
  shuffled_plan = [
    lesson(hour=8),
    lesson(hour=10, group="G2", course="Sieci", building="B", room="3"),
    lesson(hour=9, course="Wykład", room="2"),
  ]
  _, warnings = validate(shuffled_plan)
  assert "Anna w Pon: 1 zmian(y) budynku" in warnings
