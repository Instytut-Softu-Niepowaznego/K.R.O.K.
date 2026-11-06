"""Wspólne ustawienia konsoli dla CLI (krok-gen, krok-events, krok-env)."""

import functools
import os
import sys
from pathlib import Path


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


def closed_pipe_safe(main):
  """Dekorator `main` CLI: zamknięty potok (`krok-… | head`) kończy pracę cicho z kodem 0, zamiast kodu 120
  i „Exception ignored … BrokenPipeError” przy zamykaniu stdout. Wyjście trzeba opróżnić jeszcze w środku."""
  @functools.wraps(main)
  def wrapper(*args, **kwargs):
    try:
      code = main(*args, **kwargs)
      sys.stdout.flush()
      return code
    except BrokenPipeError:
      output_closed()
      return 0
  return wrapper


def write_outputs(out_dir: Path, files: dict[str, str]) -> str | None:
  """Zapisuje pliki wyjściowe do katalogu. Zwraca opis błędu (np. --out wskazuje plik, brak praw) albo None."""
  try:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
      (out_dir / name).write_text(text, encoding="utf-8")
  except OSError as e:
    return f"Nie można zapisać wyników do {out_dir}: {e.strerror or e}"
  return None


def describe(error: Exception) -> str:
  """Krótki opis błędu walidacji pydantic albo innego wyjątku do komunikatu CLI."""
  errors = getattr(error, "errors", None)
  if callable(errors) and hasattr(error, "error_count"):
    first = error.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "(całość)"
    return f"{error.error_count()} błędów walidacji, np. {where}: {first['msg']}"
  return str(error)
