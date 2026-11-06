import json
from pathlib import Path

import pytest

from krok.db import apply_events, connect, import_dir
from krok.events import generate_events
from krok.generator import generate, load_preset, write
from krok.perception.quality import (QualityParams, build_quality_report, event_category, issue_category, load_manifests,
                                     render_markdown, render_text)
from krok.perception.quality_cli import main


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
def test_every_injected_problem_is_detected(tmp_path, name, seed):
  """Kryteria, scenariusz „Dane z błędami”: wykryte 100% wprowadzonych błędów znanych typów, bez fałszywych alarmów."""
  r = pipeline(tmp_path, name, seed)
  noise, events = r["detection"]["noise"], r["detection"]["events"]
  for det in (noise, events):
    assert det["missed"] == [], det["missed"]
    assert det["recall"] in (1.0, None)
    assert det["notInManifest"] == 0, det["notInManifestExamples"]
  assert noise["introduced"] > 0
  # W kategorii „skutki” są zdarzenia spoza manifestu wskazujące rekordy odrzucone przy imporcie oraz celowe usterki
  # zasłonięte takim odrzuceniem.
  assert r["events"]["categories"]["consequences"]["total"] == events["consequencesOfImport"] + events["maskedByImport"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_feasibility_obstacles_have_known_cause(tmp_path, seed):
  """Na danych z szumem i zdarzeniami każda przeszkoda wynika z jakości danych albo ze zdarzenia."""
  r = pipeline(tmp_path, seed=seed)
  f = r["feasibility"]
  assert f["causes"]["unexplained"] == 0, f["causeExamples"]
  assert sum(f["causes"].values()) == f["errors"]


def test_clean_data_obstacles_come_only_from_events(tmp_path):
  f = pipeline(tmp_path, noise=False)["feasibility"]
  assert f["causes"] == {"dataQuality": 0, "events": f["errors"], "lostRecords": 0, "unexplained": 0}


def test_categories():
  assert issue_category("outlier", "flagged") == "uncertain"
  assert issue_category("outlier", "rejected") == "errors"
  assert issue_category("missingField", "rejected") == "missing"
  assert issue_category("missingField", "flagged") == "uncertain"
  assert issue_category("nearDuplicate", "fixed") == "duplicates"
  assert issue_category("cascade", "rejected") == "consequences"
  assert issue_category("nowyRodzaj", "rejected") == "errors"
  assert event_category("duplicate", "duplicate") == "duplicates"
  assert event_category("applied", "outOfOrder") == "uncertain"
  assert event_category("rejected", "missingField") == "missing"
  assert event_category("rejected", "badReference", "nieznane rooms/P1-004", frozenset({"P1-004"})) == "consequences"
  assert event_category("rejected", "badReference", "nieznane rooms/P1-004X", frozenset({"P1-004"})) == "errors"


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


def test_room_capacity_problems_are_listed(tmp_path):
  r = pipeline(tmp_path, "kierunek", 1)
  manifest = json.loads((tmp_path / "kierunek-1" / "_noise.json").read_text())
  broken = {c["id"] for c in manifest if c["file"] == "rooms.json" and c["field"] == "capacity"}
  listed = set(r["sensitivity"]["roomCapacity"]["rejected"]) | set(r["sensitivity"]["roomCapacity"]["flagged"])
  assert broken and broken <= listed


def test_markdown_and_text_render(tmp_path):
  r = pipeline(tmp_path)
  md = render_markdown(r)
  for heading in ("# Raport jakości danych", "## Problemy z importu", "## Problemy ze zdarzeń", "## Wykrywalność",
                  "## Wykonalność (#10)", "## Wrażliwość na ograniczenia percepcji"):
    assert heading in md
  for code in ("L1", "L2", "L3", "L4", "L5", "L6"):
    assert f"| {code} |" in md
  assert "Wykrywalność (noise)" in render_text(r)


# --- CLI ---

def test_cli_data_with_events_and_manifests(tmp_path, capsys):
  from krok.events.cli import main as events_main
  from krok.generator.cli import main as gen_main
  assert events_main(["generate", "--preset", "mini", "--seed", "1", "--out", str(tmp_path / "czyste"), "-q"]) == 0
  assert gen_main(["--preset", "mini", "--seed", "1", "--noise", "--out", str(tmp_path / "szum"), "-q"]) == 0
  assert main(["--data", str(tmp_path / "szum"), "--events", str(tmp_path / "czyste" / "events.jsonl")]) == 0
  out = capsys.readouterr().out
  assert "Wykrywalność (noise)" in out and "Wykrywalność (events)" in out
  report = json.loads((tmp_path / "szum" / "_quality.json").read_text())
  assert set(report["detection"]) == {"noise", "events"}
  assert (tmp_path / "szum" / "quality.md").read_text().startswith("# Raport jakości danych")


def test_cli_reads_existing_database(tmp_path, capsys):
  from krok.db.cli import main as db_main
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path / "dane"), "-q"]) == 0
  db = tmp_path / "krok.db"
  assert db_main(["--db", str(db), "import", str(tmp_path / "dane")]) == 0
  capsys.readouterr()
  assert main(["--db", str(db), "--out", str(tmp_path / "raport"), "-q"]) == 0
  report = json.loads((tmp_path / "raport" / "_quality.json").read_text())
  assert report["source"] == str(db) and report["imported"]["issues"] == 0


@pytest.mark.parametrize("args", [
  ["--db", "{tmp}/nie-ma.db"],
  ["--data", "{tmp}/nie-ma"],
  ["--data", "{tmp}", "--events", "{tmp}/nie-ma.jsonl"],
  ["--events", "{tmp}/x.jsonl"],
  ["--data", "{tmp}", "--availability-margin", "0"],
  ["--data", "{tmp}", "--group-growth", "-0.1"],
])
def test_cli_bad_input_is_exit_2(tmp_path, args):
  assert main([a.replace("{tmp}", str(tmp_path)) for a in args]) == 2


def test_cli_data_without_config_is_exit_2(tmp_path):
  (tmp_path / "groups.json").write_text("[]")
  assert main(["--data", str(tmp_path), "-q"]) == 2


def test_noise_consequences_and_masked_events_are_not_false_alarms(tmp_path):
  """kierunek/17: popsuty programId grupy odrzuca też jej dzieci (skutki szumu, nie fałszywe alarmy).
  kierunek/5: zdarzenie cofnięte w czasie wskazuje rekord odrzucony przy imporcie (usterka zasłonięta, nie przeoczona)."""
  noise = pipeline(tmp_path / "a", "kierunek", 17)["detection"]["noise"]
  assert noise["consequencesOfNoise"] > 0 and noise["notInManifest"] == 0
  events = pipeline(tmp_path / "b", "kierunek", 5)["detection"]["events"]
  assert events["maskedByImport"] >= 1 and events["missed"] == [] and events["recall"] == 1.0
  assert events["masked"] == events["maskedByImport"] and events["undetectable"] == 0  # zasłonięte ≠ niewykrywalne z zasady


def test_ids_in_message_match_whole_ids():
  from krok.perception.quality import _ids_in
  assert "P-0001" in _ids_in("nieznane lecturers/P-0001") and "P-0001" not in _ids_in("nieznane lecturers/P-00011")
  assert "WJ-INF-inz-s1-C2" in _ids_in("rodzic WJ-INF-inz-s1-C2 z innego programu")


# --- uwagi z recenzji (#33) ---

def test_referenced_id_keeps_spaces_and_slashes():
  from krok.perception.quality import _referenced_id
  assert _referenced_id("nieznane rooms/P1-004") == "P1-004"
  assert _referenced_id("nieznane rooms/P1 004 extra") == "P1 004 extra"
  assert _referenced_id("nieznane rooms/P1/101") == "P1/101"  # stary format id sali
  assert _referenced_id("nieznany typ sali lab-it") is None and _referenced_id(None) is None


def test_group_block_from_event_is_attributed_to_events(tmp_path):
  """calendarExceptionAdded z blokadą rocznika na cały semestr: przeszkoda ma przyczynę „zdarzenia”, nie „nieznana”."""
  preset = load_preset("mini")
  ds = generate(preset).dataset
  write(generate(preset), tmp_path / "src")
  cohort = next(g for g in ds.groups if g.kind == "cohort")
  block = {"id": "blokada-rocznika", "type": "resourceBlock", "from": ds.calendar.semester_start.isoformat(),
           "to": ds.calendar.semester_end.isoformat(), "scope": {"groupIds": [cohort.id]}, "label": "Blokada"}
  line = json.dumps({"id": "E-1", "timestamp": "2026-09-30T10:00:00", "type": "calendarExceptionAdded",
                     "payload": {"exception": block}})
  engine = connect(tmp_path / "krok.db", create=True)
  try:
    import_dir(tmp_path / "src", engine)
    apply_events(engine, [line])
    f = build_quality_report(engine)["feasibility"]
  finally:
    engine.dispose()
  assert f["byCode"].get("zablokowana-grupa") and f["causes"]["unexplained"] == 0
  assert f["causes"]["events"] == f["errors"]


def test_rejected_records_without_id_do_not_crash(tmp_path):
  from krok.db import Issue
  from krok.perception import analyze
  from krok.perception.quality import NO_ID, _feasibility, _room_capacity
  state, rep = analyze(generate(load_preset("edge-cases")).dataset)
  issues = [Issue("lecturers.json", None, "missingField", "rejected", "id", "brak id"),
            Issue("lecturers.json", "P-0099", "badReference", "rejected", None, "x"),
            Issue("rooms.json", None, "missingField", "rejected", "capacity", "brak pojemności")]
  f = _feasibility(rep, state, issues, [])
  assert sum(f["causes"].values()) == f["errors"]
  assert _room_capacity(issues)["rejected"] == [NO_ID]


@pytest.mark.parametrize("option", ["--group-growth", "--coordinate-error-m"])
@pytest.mark.parametrize("value", ["nan", "inf"])
def test_cli_rejects_non_finite_parameters(tmp_path, option, value):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0
  assert main(["--data", str(tmp_path), option, value, "-q"]) == 2
  assert not (tmp_path / "_quality.json").exists()


@pytest.mark.parametrize("content", ["{zly", '{"a": 1}', '[{"file": "x"}]', '"tekst"'])
def test_cli_invalid_noise_manifest_is_exit_2(tmp_path, content):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0
  (tmp_path / "zly.json").write_text(content)
  assert main(["--data", str(tmp_path), "--noise-manifest", str(tmp_path / "zly.json"), "-q"]) == 2


def test_cli_missing_explicit_manifest_is_exit_2_but_default_is_optional(tmp_path):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0  # bez szumu: brak _noise.json
  assert main(["--data", str(tmp_path), "-q"]) == 0
  assert main(["--data", str(tmp_path), "--noise-manifest", str(tmp_path / "nie-ma.json"), "-q"]) == 2
  assert main(["--data", str(tmp_path), "--events-manifest", str(tmp_path / "nie-ma.json"), "-q"]) == 2


def test_load_manifests_accepts_both_noise_shapes(tmp_path):
  entry = {"file": "rooms.json", "id": "P1-001", "kind": "outlier", "field": "capacity", "after": 0}
  (tmp_path / "lista.json").write_text(json.dumps([entry]))
  (tmp_path / "obiekt.json").write_text(json.dumps({"changes": [entry]}))
  (tmp_path / "_events.json").write_text(json.dumps({"errorsList": [{"line": 3, "kind": "duplicate"}]}))
  assert load_manifests(tmp_path / "lista.json", tmp_path / "_events.json") == ([entry], [{"line": 3, "kind": "duplicate"}])
  assert load_manifests(tmp_path / "obiekt.json", None)[0] == [entry]
