from ..plan import PlanInput, validate
from .base import Tool

def _run(args: PlanInput):
  errors, warnings = validate(args.lessons)
  return {"ok": not errors, "errors": errors, "warnings": warnings}

validate_plan = Tool("validate_plan",
  "Sprawdza plan: kolizje sal/prowadzących/grup, typy sal, kompetencje prowadzących, liczbę godzin. "
  "Zwraca błędy (trzeba poprawić) i ostrzeżenia (warto poprawić).", PlanInput, _run)
