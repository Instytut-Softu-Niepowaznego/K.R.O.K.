import csv

import pytest

import krok.harness.tools.save_plan as module
from krok.harness.plan import PlanInput

pytestmark = pytest.mark.usefixtures("test_data")


def read_rows(path):
  with path.open(encoding="utf-8", newline="") as f:
    return list(csv.DictReader(f))


def test_saves_sorted_csv_only_for_valid_plan(tmp_path, monkeypatch, valid_plan):
  plans_dir = tmp_path / "plans"
  monkeypatch.setattr(module, "PLANS", plans_dir)

  with pytest.raises(ValueError, match="popraw je przed zapisem"):
    module.save_plan.run(PlanInput(lessons=valid_plan[:-1]))
  assert not plans_dir.exists()

  module.save_plan.run(PlanInput(lessons=list(reversed(valid_plan))))
  by_group = read_rows(plans_dir / "plan-zajec.csv")
  assert list(by_group[0]) == module.COLUMNS
  assert [(r["group"], r["day"], r["hour"]) for r in by_group] == [("G1", "Pon", "8"), ("G1", "Pon", "9"), ("G2", "Wt", "8")]
  by_worker = read_rows(plans_dir / "plan-pracownikow.csv")
  assert [(r["worker"], r["hour"]) for r in by_worker] == [("Anna", "8"), ("Anna", "9"), ("Piotr", "8")]
