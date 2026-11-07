"""Ten sam seed daje te same pliki, inny seed inne.

Przebiegi idą w osobnych procesach z różnym PYTHONHASHSEED: w jednym procesie
kolejność zależna od haszowania (set, dict z kluczami str) jest stała i błąd by
się nie pokazał.
"""
import os
import subprocess
import sys
from pathlib import Path


def _run(module: str, args: list[str], hash_seed: str) -> None:
  """Uruchamia `main` modułu w osobnym procesie z podanym PYTHONHASHSEED."""
  env = {**os.environ, "PYTHONHASHSEED": hash_seed}
  code = f"import sys; from {module} import main; sys.exit(main(sys.argv[1:]))"
  subprocess.run([sys.executable, "-c", code, *args], env=env, check=True)


def _generate(out: Path, seed: int | None, hash_seed: str) -> dict[str, bytes]:
  """Generuje dane, zdarzenia i dane z szumem; zwraca zawartość wszystkich plików."""
  gen = ["--preset", "kierunek", *(["--seed", str(seed)] if seed is not None else []), "-q"]
  # Szum psuje walidację, więc zdarzenia powstają z czystych danych.
  _run("krok.generator.cli", [*gen, "--no-noise", "--out", str(out / "clean")], hash_seed)
  _run("krok.events.cli", ["generate", "--data", str(out / "clean"), "-q"], hash_seed)
  _run("krok.generator.cli", [*gen, "--noise", "--out", str(out / "noisy")], hash_seed)
  return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_same_seed_same_files_other_seed_differs(tmp_path):
  # kierunek z domyślnym seedem przy PYTHONHASHSEED 1 i 3 dawał różne pule prowadzących (regresja 292cbe0).
  """Ten sam seed daje te same pliki niezależnie od PYTHONHASHSEED, inny seed inne."""
  first = _generate(tmp_path / "a", None, "1")
  assert {"clean/events.jsonl", "noisy/_noise.json"} <= first.keys()
  assert _generate(tmp_path / "b", None, "3") == first
  other = _generate(tmp_path / "c", 43, "1")
  for name in ("clean/classes.json", "clean/events.jsonl", "noisy/_noise.json"):
    assert other[name] != first[name], name
