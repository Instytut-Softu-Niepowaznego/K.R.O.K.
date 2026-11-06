"""Wspólne ustawienia konsoli dla CLI (krok-gen, krok-events, krok-env)."""

import sys


def safe_output() -> None:
  """Znaki spoza kodowania terminala (np. ✓, ł przy cp1252 na Windows) zamienia na `?` zamiast przerywać program.

  Kodowanie strumienia zostaje takie, jak ustawił system albo PYTHONIOENCODING.
  """
  for stream in (sys.stdout, sys.stderr):
    try:
      stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # strumień bez reconfigure (np. podmieniony w testach)
      pass
