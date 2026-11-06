import datetime as dt
import json
from collections import defaultdict

import pytest
from pydantic import ValidationError

from krok.generator import generate, list_presets, load_preset, write
from krok.generator.calendar import easter
from krok.generator.cli import main
from krok.generator.core import load_generated
from krok.generator.curriculum import FORM_GROUP_KIND
from krok.model import dataset_from_files, reference_errors, to_minutes

CLEAN = ["mini", "kierunek", "wydzial"]


@pytest.fixture(scope="module")
def results():
  return {name: generate(load_preset(name)) for name in list_presets()}


def test_all_presets_listed():
  assert set(list_presets()) >= {"mini", "kierunek", "wydzial", "edge-cases"}


@pytest.mark.parametrize("name", ["mini", "kierunek", "wydzial", "edge-cases"])
def test_preset_is_consistent_and_roundtrips(results, name, tmp_path):
  r = results[name]
  assert reference_errors(r.dataset) == []
  write(r, tmp_path)
  assert load_generated(tmp_path) == r.dataset
  assert json.loads((tmp_path / "_meta.json").read_text())["preset"] == name


def test_same_seed_same_output_other_seed_differs():
  preset = load_preset("kierunek")
  assert generate(preset, 7).files == generate(preset, 7).files
  assert generate(preset, 7).files != generate(preset, 8).files


@pytest.mark.parametrize("name", CLEAN)
def test_group_tree_sizes(results, name):
  ds = results[name].dataset
  preset = load_preset(name)
  children = defaultdict(list)
  for g in ds.groups:
    if g.parent_id:
      children[g.parent_id].append(g)
  for g in ds.groups:
    if children[g.id]:
      assert sum(c.size for c in children[g.id]) == g.size, g.id
  limits = {"lecture": preset.groups.lecture_max, "exercise": preset.groups.exercise, "lab": preset.groups.lab}
  for g in ds.groups:
    if g.kind in limits:
      assert g.size <= limits[g.kind], g.id


@pytest.mark.parametrize("name", CLEAN)
def test_typical_data_has_rooms_and_lecturers(results, name):
  """Bez przypadków brzegowych każde zajęcia mają salę, która mieści grupę, i prowadzącego z kompetencją."""
  ds = results[name].dataset
  groups = {g.id: g for g in ds.groups}
  comp = {s.id: s.competence for s in ds.subjects}
  have = {c for lec in ds.lecturers if lec.availability for c in lec.competences}
  for c in ds.classes:
    assert groups[c.group_id].kind == FORM_GROUP_KIND[c.form]
    assert any(r.type == c.room_type and r.capacity >= groups[c.group_id].size for r in ds.rooms), c.id
    assert comp[c.subject_id] in have, c.id


@pytest.mark.parametrize("name", CLEAN)
def test_part_time_competences_have_weekend_lecturers(results, name):
  ds = results[name].dataset
  mode = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: mode[g.program_id] for g in ds.groups}
  comp = {s.id: s.competence for s in ds.subjects}
  weekend = {c for lec in ds.lecturers if any(a.day in ("So", "Nd") for a in lec.availability) for c in lec.competences}
  for c in ds.classes:
    if gmode[c.group_id] == "part-time":
      assert comp[c.subject_id] in weekend, c.id


@pytest.mark.parametrize("name", CLEAN)
def test_availability_inside_day(results, name):
  ds = results[name].dataset
  grid = ds.config.time_grid
  for lec in ds.lecturers:
    days = [a.day for a in lec.availability]
    assert len(days) == len(set(days)), lec.id
    for a in lec.availability:
      assert to_minutes(grid.day_start) <= to_minutes(a.from_) < to_minutes(a.to) <= to_minutes(grid.day_end)
      assert (to_minutes(a.from_) - to_minutes(grid.day_start)) % grid.start_step_minutes == 0


def test_kierunek_has_both_modes_and_degrees(results):
  ds = results["kierunek"].dataset
  assert {(p.degree, p.study_mode) for p in ds.programs} == {
    ("inz", "full-time"), ("inz", "part-time"), ("mgr", "full-time"), ("mgr", "part-time")}
  assert any(c.parity != "every" for c in ds.classes)
  cal = ds.calendar
  assert cal.part_time_sessions.first_weekend.weekday() == 5
  assert {e.type for e in cal.exceptions} == {"holiday", "rector", "dayOverride", "resourceBlock"}


def test_edge_cases_are_all_injected_and_point_to_real_ids(results):
  r = results["edge-cases"]
  ds = r.dataset
  cases = {c["case"]: c for c in r.meta["edgeCases"]}
  assert set(cases) == {"grupa-wieksza-od-sal", "przedmiot-bez-prowadzacego", "prowadzacy-bez-dostepnosci",
                        "limit-ponizej-obciazenia", "zajecia-1-jednostkowe", "zajecia-ab", "prowadzacy-dzienne-i-zaoczne",
                        "zajecia-dluzsze-niz-dostepnosc", "nakladajace-sie-wyjatki", "odpracowanie-w-dniu-wolnym"}
  known = {x.id for coll in (ds.groups, ds.lecturers, ds.subjects, ds.classes, ds.calendar.exceptions) for x in coll}
  for c in cases.values():
    assert set(c["ids"]) <= known, c["case"]

  lecturers = {lec.id: lec for lec in ds.lecturers}
  groups = {g.id: g for g in ds.groups}
  big = groups[cases["grupa-wieksza-od-sal"]["ids"][0]]
  room_type = next(c.room_type for c in ds.classes if c.id == cases["grupa-wieksza-od-sal"]["ids"][1])
  assert all(r.capacity < big.size for r in ds.rooms if r.type == room_type)
  assert lecturers[cases["prowadzacy-bez-dostepnosci"]["ids"][0]].availability == []
  over = lecturers[cases["limit-ponizej-obciazenia"]["ids"][0]]
  assert sum(c.units_per_week for c in ds.classes if c.lecturer_id == over.id) > over.max_units_weekly


def test_noise_changes_only_noisy_files_and_is_logged():
  preset = load_preset("kierunek")
  clean = generate(preset)
  preset.noise.enabled = True
  preset.noise.missing_field_rate = 0.05
  noisy = generate(preset)
  assert noisy.dataset == clean.dataset  # czyste dane się nie zmieniają, szum ma osobny strumień losowy
  assert noisy.noise and noisy.meta["noise"]["changes"] == len(noisy.noise)
  assert {n["kind"] for n in noisy.noise} >= {"missingField", "keyTypo", "badReference", "outlier"}
  with pytest.raises(ValidationError):
    dataset_from_files(noisy.files)


def test_easter_and_holidays():
  assert easter(2026) == dt.date(2026, 4, 5)
  assert easter(2027) == dt.date(2027, 3, 28)
  preset = load_preset("mini")
  preset.calendar.semester_start, preset.calendar.semester_end = dt.date(2027, 2, 22), dt.date(2027, 6, 20)
  preset.calendar.breaks = []
  cal = generate(preset).dataset.calendar
  holidays = {e.from_ for e in cal.exceptions if e.type == "holiday"}
  assert {dt.date(2027, 3, 29), dt.date(2027, 5, 3), dt.date(2027, 5, 27)} <= holidays


def test_cli(tmp_path, capsys):
  assert main(["--preset", "mini", "--seed", "1", "--out", str(tmp_path), "--noise"]) == 0
  out = capsys.readouterr().out
  assert "Preset mini, seed 1" in out and "Szum:" in out
  assert (tmp_path / "classes.json").exists() and (tmp_path / "_noise.json").exists()
  assert main(["--preset", "nie-ma-takiego"]) == 2
  assert main(["--list"]) == 0
