import csv

import pytest

import krok.harness.tools.save_plan as module
from krok.harness.plan import PlanInput


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
  path = tmp_path / "plans"
  monkeypatch.setattr(module, "PLANS", path)
  return path


def read_rows(path):
  with path.open(encoding="utf-8", newline="") as f:
    return list(csv.DictReader(f))


def test_writes_both_files_with_all_columns(plans_dir, valid_plan):
  result = module.save_plan.run(PlanInput(lessons=valid_plan))

  assert "3 lekcji" in result
  for name in ["plan-zajec.csv", "plan-pracownikow.csv"]:
    rows = read_rows(plans_dir / name)
    assert len(rows) == 3
    assert list(rows[0]) == module.COLUMNS


def test_files_are_sorted_by_owner_then_time(plans_dir, valid_plan):
  module.save_plan.run(PlanInput(lessons=list(reversed(valid_plan))))

  by_group = read_rows(plans_dir / "plan-zajec.csv")
  assert [(r["group"], r["day"], r["hour"]) for r in by_group] == [
    ("G1", "Pon", "8"), ("G1", "Pon", "9"), ("G2", "Wt", "8"),
  ]
  by_worker = read_rows(plans_dir / "plan-pracownikow.csv")
  assert [(r["worker"], r["hour"]) for r in by_worker] == [
    ("Anna", "8"), ("Anna", "9"), ("Piotr", "8"),
  ]


def test_invalid_plan_is_rejected_and_nothing_is_written(plans_dir, valid_plan):
  with pytest.raises(ValueError, match="popraw je przed zapisem"):
    module.save_plan.run(PlanInput(lessons=valid_plan[:-1]))

  assert not plans_dir.exists()
