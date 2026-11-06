"""Determinizm między procesami.

Testy w jednym procesie nie złapią kolejności zależnej od haszowania (set, dict
z kluczami str), bo PYTHONHASHSEED jest wtedy stały. Tu każdy przebieg to osobny
proces z innym PYTHONHASHSEED.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(module: str, args: list[str], hash_seed: str) -> None:
  env = {**os.environ, "PYTHONHASHSEED": hash_seed}
  code = f"import sys; from {module} import main; sys.exit(main(sys.argv[1:]))"
  subprocess.run([sys.executable, "-c", code, *args], env=env, check=True)


def _files(path: Path) -> dict[str, bytes]:
  return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("preset", ["mini", "edge-cases"])
def test_generator_and_events_same_across_hash_seeds(tmp_path, preset):
  outs = []
  for hash_seed in ("1", "2"):
    out = tmp_path / hash_seed
    gen = ["--preset", preset, "--seed", "42", "-q"]
    # Szum psuje walidację, więc zdarzenia powstają z czystych danych.
    _run("krok.generator.cli", [*gen, "--no-noise", "--out", str(out / "clean")], hash_seed)
    _run("krok.events.cli", ["generate", "--data", str(out / "clean"), "-q"], hash_seed)
    _run("krok.generator.cli", [*gen, "--noise", "--out", str(out / "noisy")], hash_seed)
    outs.append(_files(out))
  assert {"clean/events.jsonl", "noisy/_noise.json"} <= outs[0].keys()
  assert outs[0] == outs[1]
