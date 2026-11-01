import pytest
from pydantic import ValidationError

from conftest import lesson


@pytest.mark.parametrize("hour", [8, 17])
def test_accepts_boundary_hours(hour):
  assert lesson(hour=hour).hour == hour


@pytest.mark.parametrize("hour", [7, 18])
def test_rejects_hour_outside_range(hour):
  with pytest.raises(ValidationError):
    lesson(hour=hour)


def test_rejects_unknown_day():
  with pytest.raises(ValidationError):
    lesson(day="Nd")
