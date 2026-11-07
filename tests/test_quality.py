from pathlib import Path

import pytest

from krok.db import apply_events, connect, import_dir
from krok.events import generate_events
from krok.generator import generate, load_preset, write
from krok.perception.quality import QualityParams, build_quality_report, load_manifests


def pipeline(tmp_path: Path, name: str = "kierunek", seed: int = 1, *, noise: bool = True, events: bool = True,
             params: QualityParams = QualityParams()) -> dict:
  """Dane (z szumem) → import do bazy → zdarzenia z czystych danych → raport jakości z manifestami."""
  preset = load_preset(name)
  clean = generate(preset, seed)
  preset.noise.enabled = noise
  src = tmp_path / f"{name}-{seed}"
  write(generate(preset, seed), src)
  engine = connect(tmp_path / f"{name}-{seed}.db", create=True)
  try:
    import_dir(src, engine)
    events_manifest = None
    if events:
      stream = generate_events(clean.dataset, preset, seed)
      apply_events(engine, stream.lines)
      events_manifest = stream.manifest
    noise_manifest, _ = load_manifests(src / "_noise.json", None)
    return build_quality_report(engine, noise_manifest=noise_manifest, events_manifest=events_manifest, params=params)
  finally:
    engine.dispose()


def totals(section: dict) -> dict:
  return {cat: c["total"] for cat, c in section["categories"].items()}


def test_clean_data_without_events_has_no_problems(tmp_path):
  r = pipeline(tmp_path, noise=False, events=False)
  assert r["imported"]["issues"] == 0 and set(totals(r["imported"]).values()) == {0}
  assert r["events"]["lines"] == 0
  assert r["detection"] == {}  # bez manifestów nie ma czego mierzyć
  assert r["feasibility"]["verdict"] == "no-obstacles-found"


@pytest.mark.parametrize("name, seed", [("mini", 1), ("mini", 2), ("kierunek", 1), ("kierunek", 2), ("kierunek", 3),
                                        ("kierunek", 5), ("kierunek", 17)])
def test_every_injected_problem_is_detected_and_explained(tmp_path, name, seed):
  """Kryteria, scenariusz „Dane z błędami”: wykryte 100% wprowadzonych błędów znanych typów, bez fałszywych alarmów,
  a każda przeszkoda wykonalności wynika z jakości danych albo ze zdarzenia.
  kierunek/17: odrzucona grupa zabiera dzieci (skutki szumu). kierunek/5: zdarzenie wskazuje rekord odrzucony przy imporcie."""
  r = pipeline(tmp_path, name, seed)
  noise, events = r["detection"]["noise"], r["detection"]["events"]
  for det in (noise, events):
    assert det["missed"] == [], det["missed"]
    assert det["recall"] in (1.0, None)
    assert det["notInManifest"] == 0, det["notInManifestExamples"]
  assert noise["introduced"] > 0
  assert r["events"]["categories"]["consequences"]["total"] == events["consequencesOfImport"] + events["maskedByImport"]
  assert events["masked"] == events["maskedByImport"] and events["undetectable"] == 0
  f = r["feasibility"]
  assert f["causes"]["unexplained"] == 0, f["causeExamples"]
  assert sum(f["causes"].values()) == f["errors"]


def test_sensitivity_measures(tmp_path):
  r = pipeline(tmp_path, noise=False, events=True, params=QualityParams(coordinate_error_m=30, group_growth=0.1))
  s = r["sensitivity"]
  # 2 × 0,03 km × routeFactor 1,3 / 5 km/h × 60
  assert s["coordinates"]["travelErrorMinutes"] == pytest.approx(2 * 0.03 * 1.3 / 5 * 60, abs=0.01)
  assert s["coordinates"]["pairs"] == 15  # 6 budynków
  assert s["eventsBeforeEffectiveDate"]["takeEffectLater"] > 0
  none = pipeline(tmp_path / "zero", noise=False, events=False, params=QualityParams(group_growth=0))
  assert none["sensitivity"]["groupGrowth"]["atRisk"] == 0
  big = pipeline(tmp_path / "duzo", noise=False, events=False, params=QualityParams(group_growth=1.0))
  assert big["sensitivity"]["groupGrowth"]["atRisk"] > s["groupGrowth"]["atRisk"] > 0
