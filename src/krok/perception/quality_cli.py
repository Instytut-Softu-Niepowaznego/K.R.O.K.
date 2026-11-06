"""uv run krok-quality --db data/krok.db
   uv run krok-quality --data data/generated/kierunek-szum --events data/generated/kierunek/events.jsonl"""

import argparse
import math
import sys
import tempfile
from pathlib import Path

from krok.console import safe_output
from krok.db import ImportFailed, apply_events, connect, db_path, import_dir
from krok.model import dump_json

from .quality import ManifestError, QualityParams, build_quality_report, load_manifests, render_markdown, render_text

REPORT_FILE = "_quality.json"
MARKDOWN_FILE = "quality.md"


def _report(engine, args, noise_path, events_path) -> dict:
  # Manifest podany wprost musi istnieć. Domyślne (z katalogu --data, obok --events) są opcjonalne.
  noise, events = load_manifests(noise_path, events_path, noise_required=args.noise_manifest is not None,
                                 events_required=args.events_manifest is not None)
  params = QualityParams(coordinate_error_m=args.coordinate_error_m, group_growth=args.group_growth,
                         availability_margin=args.availability_margin)
  return build_quality_report(engine, noise_manifest=noise, events_manifest=events, params=params)


def main(argv: list[str] | None = None) -> int:
  """Punkt wejścia `krok-quality`; zwraca 0, a 2 przy złych danych lub argumentach."""
  safe_output()
  p = argparse.ArgumentParser(prog="krok-quality", description="Raport jakości danych (K.R.O.K. #11)")
  src = p.add_mutually_exclusive_group()
  src.add_argument("--db", type=Path, help="baza po krok-db import/events (domyślnie $KROK_DB albo data/krok.db)")
  src.add_argument("--data", type=Path, help="katalog z danymi: import do tymczasowej bazy, bez zmiany data/krok.db")
  p.add_argument("--events", type=Path, help="z --data: strumień zdarzeń do zastosowania po imporcie")
  p.add_argument("--noise-manifest", type=Path, help="_noise.json (domyślnie z katalogu --data)")
  p.add_argument("--events-manifest", type=Path, help="_events.json (domyślnie obok pliku --events)")
  p.add_argument("--out", type=Path, help=f"zapisz {REPORT_FILE} i {MARKDOWN_FILE} (domyślnie katalog --data)")
  p.add_argument("--coordinate-error-m", type=float, default=QualityParams.coordinate_error_m,
                 help="możliwy błąd współrzędnych budynków w metrach (domyślnie %(default)s)")
  p.add_argument("--group-growth", type=float, default=QualityParams.group_growth,
                 help="o ile grupy mogą urosnąć po zapisach, np. 0.1 = 10%% (domyślnie %(default)s)")
  p.add_argument("--availability-margin", type=float, default=QualityParams.availability_margin,
                 help="wykorzystanie dostępności, od którego kompetencja jest wrażliwa (domyślnie %(default)s)")
  p.add_argument("-q", "--quiet", action="store_true")
  args = p.parse_args(argv)

  for name in ("coordinate_error_m", "group_growth"):
    value = getattr(args, name)
    if not math.isfinite(value) or value < 0:  # nan/inf wywracały obliczenia i dawały niepoprawny JSON
      print(f"--{name.replace('_', '-')} musi być skończoną liczbą ≥ 0", file=sys.stderr)
      return 2
  if not 0 < args.availability_margin <= 1:
    print("--availability-margin musi być z zakresu (0, 1]", file=sys.stderr)
    return 2
  if args.events and not args.data:
    print("--events działa tylko z --data; przy --db zdarzenia stosuje krok-db events", file=sys.stderr)
    return 2
  if args.events and not args.events.exists():
    print(f"Nie ma pliku {args.events}", file=sys.stderr)
    return 2

  events_manifest = args.events_manifest or (args.events.parent / "_events.json" if args.events else None)
  if args.data:
    if not args.data.is_dir():
      print(f"Nie ma katalogu {args.data}", file=sys.stderr)
      return 2
    with tempfile.TemporaryDirectory() as tmp:
      engine = connect(Path(tmp) / "quality.db", create=True)
      try:
        try:
          import_dir(args.data, engine)
        except ImportFailed as e:
          print(e, file=sys.stderr)
          return 2
        if args.events:
          apply_events(engine, args.events)
        try:
          report = _report(engine, args, args.noise_manifest or args.data / "_noise.json", events_manifest)
        except ManifestError as e:
          print(e, file=sys.stderr)
          return 2
      finally:
        engine.dispose()
  else:
    try:
      engine = connect(args.db)
    except FileNotFoundError as e:
      print(e, file=sys.stderr)
      return 2
    try:
      report = _report(engine, args, args.noise_manifest, events_manifest)
    except ManifestError as e:
      print(e, file=sys.stderr)
      return 2
    finally:
      engine.dispose()
    report["source"] = str(db_path(args.db))

  out = args.out or args.data
  if out:
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_FILE).write_text(dump_json(report), encoding="utf-8")
    (out / MARKDOWN_FILE).write_text(render_markdown(report) + "\n", encoding="utf-8")
  if not args.quiet:
    print(render_text(report))
    if out:
      print(f"\nRaport: {out / MARKDOWN_FILE}, {out / REPORT_FILE}")
  return 0


if __name__ == "__main__":
  sys.exit(main())

