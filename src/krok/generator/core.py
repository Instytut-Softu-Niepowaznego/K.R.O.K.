"""Złożenie całego wydziału z presetu."""

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from krok.model import Building, Config, Dataset, Room, dataset_from_files, dump_json, reference_errors, to_minutes

from . import edge_cases
from .calendar import build_calendar
from .curriculum import build_curriculum
from .lecturers import build_lecturers, class_load, class_modes
from .noise import apply_noise
from .pools import build_pools
from .settings import BASE_DATA_DIR, Preset, load_catalog
from .structure import build_structure

GENERATOR_VERSION = 1


@dataclass
class Result:
  dataset: Dataset
  meta: dict
  files: dict[str, object]
  noise: list[dict] = field(default_factory=list)


def _stream(seed: int, stage: str) -> random.Random:
  """Osobny strumień losowy na etap: zmiana jednego etapu nie przetasowuje pozostałych."""
  return random.Random(f"{seed}:{stage}")


def _read_json(path: Path):
  return json.loads(path.read_text(encoding="utf-8"))


def load_base(preset: Preset, rng: random.Random, buildings_path: Path | None = None, rooms_path: Path | None = None):
  base = BASE_DATA_DIR / preset.base_data
  config = Config.model_validate(_read_json(base / "config.json"))
  buildings = [Building.model_validate(b) for b in _read_json(buildings_path or base / "buildings.json")]
  rooms = []
  for raw in _read_json(rooms_path or base / "rooms.json"):
    if raw.get("capacity") is None:
      raw = {**raw, "capacity": rng.randint(*preset.room_capacity[raw["type"]])}
    rooms.append(Room.model_validate(raw))
  return config, buildings, rooms


def generate(preset: Preset, seed: int | None = None, *, buildings_path: Path | None = None,
             rooms_path: Path | None = None) -> Result:
  seed = preset.seed if seed is None else seed
  catalog = load_catalog(preset.catalog)
  config, buildings, rooms = load_base(preset, _stream(seed, "rooms"), buildings_path, rooms_path)

  faculties, fields, programs, groups, cohorts = build_structure(preset, _stream(seed, "structure"))
  subjects, classes = build_curriculum(preset, catalog, cohorts, _stream(seed, "curriculum"))
  lecturers = build_lecturers(preset, catalog, config, buildings, subjects, classes, groups, programs, _stream(seed, "lecturers"))
  build_pools(preset, subjects, classes, lecturers, groups, programs, _stream(seed, "pools"))
  calendar = build_calendar(preset, rooms, _stream(seed, "calendar"))

  cases: list[dict] = []
  if preset.edge_cases.enabled:
    world = edge_cases.World(preset.faculty_id, groups, programs, rooms, lecturers, subjects, classes, calendar)
    cases = edge_cases.inject(world)
    subjects, classes = world.subjects, world.classes

  # Ponowna walidacja całości: łapie też zmiany wprowadzone po utworzeniu obiektów.
  ds = Dataset.model_validate(Dataset(
    config=config, calendar=calendar, faculties=faculties, fields=fields, programs=programs, groups=groups,
    buildings=buildings, rooms=rooms, lecturers=lecturers, subjects=subjects, classes=classes,
  ).model_dump())
  errors = reference_errors(ds)
  if errors:
    raise ValueError("Generator zbudował niespójne dane:\n" + "\n".join(errors))

  files = ds.files()
  noise: list[dict] = []
  if preset.noise.enabled:
    files, noise = apply_noise(files, preset.noise, _stream(seed, "noise"))

  meta = {
    "generatorVersion": GENERATOR_VERSION,
    "preset": preset.name,
    "seed": seed,
    "counts": {k: len(getattr(ds, k)) for k in ("programs", "groups", "rooms", "lecturers", "subjects", "classes")},
    "edgeCases": cases,
    "noise": {"enabled": preset.noise.enabled, "changes": len(noise)},
  }
  return Result(ds, meta, files, noise)


def write(result: Result, out_dir: Path) -> list[Path]:
  out_dir.mkdir(parents=True, exist_ok=True)
  written = []
  extra = {"_meta.json": result.meta}
  if result.noise:
    extra["_noise.json"] = result.noise
  else:
    (out_dir / "_noise.json").unlink(missing_ok=True)
  for name, data in {**result.files, **extra}.items():
    path = out_dir / name
    path.write_text(dump_json(data), encoding="utf-8")
    written.append(path)
  return written


def summary(ds: Dataset) -> str:
  """Krótki bilans: zapotrzebowanie na sale i prowadzących vs dostępne zasoby (szczegółowo liczy to #10)."""
  grid = ds.config.time_grid
  day_minutes = to_minutes(grid.day_end) - to_minutes(grid.day_start)
  units_per_day = day_minutes // (grid.unit_minutes + grid.breaks.min_between_classes_minutes)
  modes = class_modes(ds.classes, ds.groups, ds.programs)
  comp = {s.id: s.competence for s in ds.subjects}

  lines = [f"programy {len(ds.programs)}, grupy {len(ds.groups)}, sale {len(ds.rooms)}, prowadzący {len(ds.lecturers)}, "
           f"przedmioty {len(ds.subjects)}, zajęcia {len(ds.classes)}", "",
           "Sale (jednostki/tydzień: potrzebne / dostępne, obłożenie):"]
  need: dict[tuple[str, str], float] = defaultdict(float)
  for c in ds.classes:
    need[(c.room_type, modes[c.id])] += class_load(c)
  rooms_by_type = defaultdict(int)
  for r in ds.rooms:
    rooms_by_type[r.type] += 1
  for mode in ("full-time", "part-time"):
    days = len(grid.days_by_study_mode[mode])
    for rt in ds.config.room_types:
      n = need.get((rt, mode), 0)
      if not n:
        continue
      have = rooms_by_type[rt] * days * units_per_day
      pct = f"{100 * n / have:.0f}%" if have else "BRAK SAL"
      lines.append(f"  {mode:9} {rt:16} {n:7.1f} / {have:5}  {pct}")

  lines += ["", "Prowadzący wg kompetencji (jednostki/tydzień: potrzebne / suma limitów):"]
  need_c: dict[str, float] = defaultdict(float)
  for c in ds.classes:
    need_c[comp[c.subject_id]] += class_load(c)
  for k in sorted(need_c):
    cap = sum(lec.max_units_weekly for lec in ds.lecturers if k in lec.competences)
    lines.append(f"  {k:28} {need_c[k]:7.1f} / {cap:5}" + ("  BRAK PROWADZĄCYCH" if not cap else ""))
  return "\n".join(lines)


def load_generated(out_dir: Path) -> Dataset:
  """Wczytuje wygenerowane (czyste) dane z powrotem do modeli."""
  from krok.model import read_files
  return dataset_from_files(read_files(out_dir))
