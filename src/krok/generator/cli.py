"""uv run krok-gen --preset wydzial --seed 42 --out data/generated/wydzial"""

import argparse
import sys
from pathlib import Path

from krok.console import safe_output

from .core import generate, summary, write
from .settings import list_presets, load_preset


def main(argv: list[str] | None = None) -> int:
  safe_output()
  p = argparse.ArgumentParser(prog="krok-gen", description="Generator danych wydziału (K.R.O.K. #7)")
  p.add_argument("--preset", default="mini", help="nazwa presetu albo ścieżka do pliku .toml (domyślnie: mini)")
  p.add_argument("--seed", type=int, help="ziarno losowania (domyślnie z presetu)")
  p.add_argument("--out", type=Path, help="katalog wyjściowy (domyślnie data/generated/<preset>)")
  p.add_argument("--buildings", type=Path, help="inny plik buildings.json")
  p.add_argument("--rooms", type=Path, help="inny plik rooms.json")
  p.add_argument("--edge-cases", action=argparse.BooleanOptionalAction, help="wymuś włączenie/wyłączenie przypadków brzegowych")
  p.add_argument("--noise", action=argparse.BooleanOptionalAction, help="wymuś włączenie/wyłączenie szumu")
  p.add_argument("--list", action="store_true", help="wypisz dostępne presety")
  p.add_argument("-q", "--quiet", action="store_true", help="bez podsumowania")
  args = p.parse_args(argv)

  if args.list:
    for name in list_presets():
      print(f"{name:12} {load_preset(name).description}")
    return 0

  try:
    preset = load_preset(args.preset)
  except FileNotFoundError as e:
    print(e, file=sys.stderr)
    return 2
  if args.edge_cases is not None:
    preset.edge_cases.enabled = args.edge_cases
  if args.noise is not None:
    preset.noise.enabled = args.noise

  result = generate(preset, args.seed, buildings_path=args.buildings, rooms_path=args.rooms)
  out = args.out or Path("data/generated") / preset.name
  write(result, out)

  if not args.quiet:
    print(f"Preset {preset.name}, seed {result.meta['seed']} → {out}/")
    print(summary(result.dataset))
    if result.meta["edgeCases"]:
      print(f"\nPrzypadki brzegowe: {len(result.meta['edgeCases'])} (lista w _meta.json)")
    if result.noise:
      print(f"Szum: {len(result.noise)} zmian (lista w _noise.json)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
