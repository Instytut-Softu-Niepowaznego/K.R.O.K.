"""uv run krok-env --data data/generated/wydzial
   uv run krok-env --preset edge-cases"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from krok.console import safe_output
from krok.generator import generate, load_preset
from krok.model import dump_json, load_dataset, reference_errors

from .feasibility import FeasibilityReport, analyze

REPORT_FILE = "_feasibility.json"
STATE_FILE = "_environment.json"


def render(report: FeasibilityReport, limit: int = 30) -> str:
  """Raport wykonalności jako tekst do terminala."""
  verdict = "PLANU NIE DA SIĘ UŁOŻYĆ" if report.verdict == "infeasible" else "brak wykrytych przeszkód"
  lines = [f"{verdict}: {report.errors} błędów, {report.warnings} ostrzeżeń ({report.compute_seconds} s)", ""]
  for p in report.problems[:limit]:
    rule = f" [{p.rule_id}]" if p.rule_id else ""
    lines.append(f"  {'✗' if p.severity == 'error' else '!'} {p.code}{rule}: {p.message}")
  if len(report.problems) > limit:
    lines.append(f"  … i {len(report.problems) - limit} kolejnych (pełna lista w {REPORT_FILE})")
  lines += ["", "Sale (minuty/tydzień: potrzebne / dostępne):"]
  for b in report.balance.rooms:
    pct = f"{b.utilization:.0%}" if b.utilization is not None else "brak sal"
    lines.append(f"  {b.study_mode:9} {b.room_type:16} {b.demand_minutes:8.0f} / {b.supply_minutes:6}  {pct}")
  lines += ["", "Kompetencje (jednostki/tydzień: potrzebne / suma limitów):"]
  for b in report.balance.competences:
    lines.append(f"  {b.competence:28} {b.demand_units:6.1f} / {b.limit_units:4}  ({b.lecturers} prowadzących)")
  if report.balance.groups:
    g = report.balance.groups[0]
    lines += ["", f"Najciaśniejszy plan studenta: {g.group_id} (tydzień {g.week}) {g.required_minutes} / {g.available_minutes} min"]
  return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
  """Punkt wejścia `krok-env`; zwraca 0 bez błędów, 1 gdy planu nie da się ułożyć, 2 przy złych danych lub argumentach."""
  safe_output()
  p = argparse.ArgumentParser(prog="krok-env", description="Stan otoczenia i raport wykonalności (K.R.O.K. #10)")
  src = p.add_mutually_exclusive_group(required=True)
  src.add_argument("--data", type=Path, help="katalog z danymi w formacie z modelu domeny (np. z krok-gen)")
  src.add_argument("--preset", help="wygeneruj dane z presetu w pamięci (bez zapisu danych)")
  p.add_argument("--seed", type=int, help="ziarno dla --preset")
  p.add_argument("--out", type=Path, help=f"zapisz {REPORT_FILE} i {STATE_FILE} do tego katalogu (domyślnie --data)")
  p.add_argument("-q", "--quiet", action="store_true", help="bez raportu w terminalu")
  args = p.parse_args(argv)

  if args.data:
    if not (args.data / "groups.json").exists():
      print(f"W {args.data} nie ma danych w formacie modelu domeny", file=sys.stderr)
      return 2
    try:
      ds = load_dataset(args.data)
    except ValidationError as e:  # przed ValueError: ValidationError dziedziczy po ValueError
      print(f"Dane w {args.data} nie przechodzą walidacji modelu ({e.error_count()} błędów). "
            "Najpierw import i czyszczenie (#9).", file=sys.stderr)
      return 2
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as e:
      print(f"Nie można wczytać danych z {args.data}: {e}", file=sys.stderr)
      return 2
  else:
    try:
      ds = generate(load_preset(args.preset), args.seed).dataset
    except (FileNotFoundError, ValueError) as e:  # brak presetu, zły TOML lub zły preset, niespójne dane
      print(f"Nie można wygenerować danych z presetu {args.preset}: {e}", file=sys.stderr)
      return 2

  broken = reference_errors(ds)
  if broken:
    print(f"Dane mają {len(broken)} błędów spójności, np. {broken[0]}. Najpierw import i czyszczenie (#9).", file=sys.stderr)
    return 2

  state, report = analyze(ds)
  out = args.out or args.data
  if out:
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_FILE).write_text(dump_json(report.model_dump(mode="json")), encoding="utf-8")
    (out / STATE_FILE).write_text(dump_json(state.export()), encoding="utf-8")
  if not args.quiet:
    print(render(report))
  return 1 if report.verdict == "infeasible" else 0


if __name__ == "__main__":
  sys.exit(main())
