"""uv run krok-events generate --preset kierunek --seed 42
   uv run krok-events replay data/generated/kierunek/events.jsonl --speed 86400"""

import argparse
import math
import sys
from pathlib import Path

from pydantic import ValidationError

from krok.console import closed_pipe_safe, describe, output_closed, safe_output, write_outputs
from krok.generator import PresetError, generate, load_preset, write
from krok.generator.settings import MAX_EVENTS_PER_WEEK
from krok.model import DatasetError, dump_json, load_dataset, reference_errors
from krok.model.dataset import read_json_file

from .generate import generate_events
from .stream import replay

EVENTS_FILE = "events.jsonl"
MANIFEST_FILE = "_events.json"


def _generate(args) -> int:
  """Komenda `generate`: dane (z --data albo z presetu) + events.jsonl i _events.json."""
  meta_path = args.data / "_meta.json" if args.data else None
  meta = {}
  if meta_path and meta_path.exists():
    try:
      meta = read_json_file(meta_path)
    except DatasetError as e:
      print(e, file=sys.stderr)
      return 2
    if not isinstance(meta, dict) or not isinstance(meta.get("preset", ""), str) or not isinstance(meta.get("seed", 0), int):
      print(f"{meta_path}: oczekiwano obiektu z polami preset (tekst) i seed (liczba całkowita)", file=sys.stderr)
      return 2
  try:
    preset = load_preset(args.preset or meta.get("preset", "kierunek"))  # ten sam preset, z którego powstały dane
  except PresetError as e:
    print(e, file=sys.stderr)
    return 2
  if args.per_week is not None:
    # nan, inf i ogromne wartości: przy nieskończonej intensywności generator nigdy by nie skończył
    if not (math.isfinite(args.per_week) and 0 < args.per_week <= MAX_EVENTS_PER_WEEK):
      print(f"--per-week musi być liczbą z zakresu (0, {MAX_EVENTS_PER_WEEK}]", file=sys.stderr)
      return 2
    preset.events.per_week = args.per_week
  if args.error_rate is not None:
    if not 0 <= args.error_rate <= 1:
      print("--error-rate musi być z zakresu 0–1", file=sys.stderr)
      return 2
    preset.events.error_rate = args.error_rate
  if args.data and not (args.data / "groups.json").exists():
    print(f"W {args.data} nie ma danych z krok-gen", file=sys.stderr)
    return 2

  if args.data:
    try:
      ds = load_dataset(args.data)
    except ValidationError as e:  # przed ValueError: ValidationError dziedziczy po ValueError
      print(f"Dane w {args.data} nie przechodzą walidacji modelu ({e.error_count()} błędów), np. dane z szumem. "
            "Zdarzenia generuj z czystych danych (ten sam preset i seed bez --noise).", file=sys.stderr)
      return 2
    except DatasetError as e:  # brak pliku, katalog, uprawnienia, kodowanie, zły JSON albo zły kształt pliku
      print(f"Nie można wczytać danych z {args.data}: {e}", file=sys.stderr)
      return 2
    broken = reference_errors(ds)
    if broken:  # np. wiszący parentId: generator zdarzeń chodzi po drzewie grup
      print(f"Dane w {args.data} mają {len(broken)} błędów spójności, np. {broken[0]}. "
            "Zdarzenia generuj z czystych danych.", file=sys.stderr)
      return 2
    seed = args.seed if args.seed is not None else meta.get("seed", preset.seed)
    out = args.out or args.data
  else:
    try:
      result = generate(preset, args.seed)
    except ValueError as e:
      print(f"Nie można wygenerować danych: {describe(e)}", file=sys.stderr)
      return 2
    ds, seed = result.dataset, result.meta["seed"]
    out = args.out or Path("data/generated") / preset.name
    try:
      write(result, out)  # zdarzenia odwołują się do tych danych, więc zapisujemy je obok
    except OSError as e:
      print(f"Nie można zapisać wyników do {out}: {e.strerror or e}", file=sys.stderr)
      return 2

  try:
    stream = generate_events(ds, preset, seed, errors=not args.no_errors)
  except ValueError as e:  # np. katalog przedmiotów z presetu, którego nie da się wczytać
    print(f"Nie można wygenerować zdarzeń: {describe(e)}", file=sys.stderr)
    return 2
  failed = write_outputs(out, {EVENTS_FILE: "\n".join(stream.lines) + "\n",
                               MANIFEST_FILE: dump_json({**stream.meta, "errorsList": stream.manifest})})
  if failed:
    print(failed, file=sys.stderr)
    return 2
  if not args.quiet:
    print(f"{stream.meta['events']} zdarzeń ({stream.meta['errors']} celowo błędnych) → {out / EVENTS_FILE}")
    for kind, n in sorted(stream.meta["types"].items()):
      print(f"  {kind:28} {n}")
  return 0


def _replay(args) -> int:
  """Komenda `replay`: wypisuje kolejne linie strumienia jako poprawne (✓) albo błędne (✗)."""
  if not args.file.exists():
    print(f"Nie ma pliku {args.file}", file=sys.stderr)
    return 2
  if args.speed is not None and (math.isnan(args.speed) or args.speed < 0):
    print("--speed musi być liczbą ≥ 0 (0 albo inf = bez czekania)", file=sys.stderr)
    return 2
  items = replay(args.file, args.speed)
  while True:
    # Błędy wejścia i wyjścia osobno: BrokenPipeError też jest OSError, a nie oznacza złego pliku.
    try:
      item = next(items)
    except StopIteration:
      return 0
    except (OSError, UnicodeDecodeError) as e:  # brak uprawnień, katalog zamiast pliku, plik nie w UTF-8
      print(f"Nie można odczytać {args.file}: {e}", file=sys.stderr)
      return 2
    if item.event and args.errors_only:
      continue
    line = (f"✓ {item.line:4} {item.event.timestamp:%Y-%m-%d %H:%M} {item.event.id} {item.event.type}" if item.event
            else f"✗ {item.line:4} {item.error}")
    try:
      print(line, flush=True)
    except BrokenPipeError:  # odbiorca nie chce więcej danych: to nie błąd odczytu
      output_closed()
      return 0


@closed_pipe_safe
def main(argv: list[str] | None = None) -> int:
  """Punkt wejścia `krok-events`; zwraca kod wyjścia (0 = sukces, 2 = złe argumenty)."""
  safe_output()
  p = argparse.ArgumentParser(prog="krok-events", description="Strumień zdarzeń z otoczenia (K.R.O.K. #8)")
  sub = p.add_subparsers(dest="command", required=True)

  g = sub.add_parser("generate", help="wygeneruj events.jsonl spójny z danymi z krok-gen")
  g.add_argument("--preset", help="preset (domyślnie: z _meta.json danych z --data, inaczej kierunek)")
  g.add_argument("--seed", type=int, help="ziarno (domyślnie z presetu albo z _meta.json danych)")
  g.add_argument("--data", type=Path, help="istniejący katalog z danymi z krok-gen (bez niego dane są generowane)")
  g.add_argument("--out", type=Path, help="katalog wyjściowy (domyślnie --data albo data/generated/<preset>)")
  g.add_argument("--per-week", type=float, help="intensywność: średnio zdarzeń na tydzień")
  g.add_argument("--error-rate", type=float, help="część zdarzeń celowo błędnych (0–1)")
  g.add_argument("--no-errors", action="store_true", help="tylko poprawne zdarzenia")
  g.add_argument("-q", "--quiet", action="store_true")
  g.set_defaults(func=_generate)

  r = sub.add_parser("replay", help="odtwórz strumień")
  r.add_argument("file", type=Path)
  r.add_argument("--speed", type=float, help="sekundy symulacji na sekundę (np. 86400 = dzień na sekundę); brak = bez czekania")
  r.add_argument("--errors-only", action="store_true", help="pokaż tylko błędne linie")
  r.set_defaults(func=_replay)

  args = p.parse_args(argv)
  return args.func(args)


if __name__ == "__main__":
  sys.exit(main())
