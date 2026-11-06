"""Konfigurowalny generator danych wydziału (#7). Wejście: preset TOML, wyjście: pliki zgodne z krok.model."""

from .core import Result, generate, summary, write
from .settings import Preset, list_presets, load_preset

__all__ = ["Result", "generate", "summary", "write", "Preset", "list_presets", "load_preset"]
