"""Wspólne ustawienia konsoli dla CLI (krok-gen, krok-events, krok-env)."""

import os
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


def output_closed() -> None:
  """Odbiorca zamknął potok (np. `krok-events replay … | head`): dalsze wypisywanie i zamknięcie stdout przy wyjściu
  nie mogą już rzucić BrokenPipeError, więc kierujemy stdout do /dev/null (wzorzec z dokumentacji Pythona)."""
  try:
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
  except (OSError, ValueError):  # stdout bez deskryptora, np. podmieniony w testach
    pass
