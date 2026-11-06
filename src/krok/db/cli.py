"""uv run krok-db import data/generated/mini && uv run krok-db events plik.jsonl && uv run krok-db report"""

import argparse
import json
import sys
from pathlib import Path

from .events import apply_events
from .importer import ImportFailed, import_dir
from .report import build_report, format_report
from .schema import connect, db_path


def main(argv: list[str] | None = None) -> int:
  p = argparse.ArgumentParser(prog="krok-db", description="Baza danych K.R.O.K. (#9)")
  p.add_argument("--db", type=Path, help="plik bazy (domyślnie $KROK_DB albo data/krok.db)")
  sub = p.add_subparsers(dest="command", required=True)
  imp = sub.add_parser("import", help="wczytaj katalog z plikami JSON, zastępując zawartość bazy")
  imp.add_argument("src", type=Path)
  ev = sub.add_parser("events", help="zastosuj strumień zdarzeń JSONL")
  ev.add_argument("file", type=Path)
  rep = sub.add_parser("report", help="pokaż raport z importu i zdarzeń")
  rep.add_argument("--json", type=Path, help="zapisz raport do pliku JSON")
  args = p.parse_args(argv)

  try:
    engine = connect(args.db, create=args.command == "import")
  except FileNotFoundError as e:
    print(e, file=sys.stderr)
    return 2
  try:
    if args.command == "import":
      try:
        ds, issues = import_dir(args.src, engine)
      except ImportFailed as e:
        print(e, file=sys.stderr)
        return 2
      print(f"Zaimportowano {args.src} -> {db_path(args.db)}: {len(ds.classes)} zajęć, {len(issues)} problemów")
    elif args.command == "events":
      print(f"Zdarzenia: {dict(apply_events(engine, args.file))}")
    else:
      report = build_report(engine)
      print(format_report(report))
      if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  finally:
    engine.dispose()
  return 0


if __name__ == "__main__":
  sys.exit(main())
