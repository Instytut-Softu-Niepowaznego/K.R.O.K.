import csv
from pathlib import Path
from ..plan import DAYS, PlanInput, validate
from .base import Tool

PLANS = Path.cwd() / "plans"
COLUMNS = ["day", "hour", "group", "course", "building", "room", "worker"]

def _write(path, lessons, key):
  with path.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(COLUMNS)
    for l in sorted(lessons, key=key):
      w.writerow([getattr(l, c) for c in COLUMNS])

def _run(args: PlanInput):
  errors, _ = validate(args.lessons)
  if errors:
    raise ValueError(f"Plan ma {len(errors)} błędów, popraw je przed zapisem: {errors}")
  PLANS.mkdir(exist_ok=True)
  _write(PLANS / "plan-zajec.csv", args.lessons, lambda l: (l.group, DAYS.index(l.day), l.hour))
  _write(PLANS / "plan-pracownikow.csv", args.lessons, lambda l: (l.worker, DAYS.index(l.day), l.hour))
  return f"Zapisano {len(args.lessons)} lekcji do plan-zajec.csv i plan-pracownikow.csv"

save_plan = Tool("save_plan", "Zapisuje poprawny plan (najpierw użyj validate_plan).", PlanInput, _run)
