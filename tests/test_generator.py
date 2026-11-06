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
from krok.generator.lecturers import available_minutes
from krok.model import dataset_from_files, reference_errors, to_minutes
from krok.perception.measures import fits, meeting_minutes


def expected_week_units(classes) -> int:
  """Niezależnie od kodu generatora: jednostki w gorszym tygodniu A lub B."""
  return max(sum(c.units_per_week for c in classes if c.parity in ("every", week)) for week in ("A", "B"))

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
                        "zajecia-dluzsze-niz-dostepnosc", "pula-niedostepna", "pula-za-maly-limit",
                        "nakladajace-sie-wyjatki", "odpracowanie-w-dniu-wolnym"}
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
  assert expected_week_units([c for c in ds.classes if c.lecturer_id == over.id]) > over.max_units_weekly

  subjects = {s.id: s for s in ds.subjects}
  sid, lid, *class_ids = cases["pula-niedostepna"]["ids"]
  form = next(c.form for c in ds.classes if c.id == class_ids[0])
  assert subjects[sid].pool(form) == [lid]
  assert all(a.day in ("So", "Nd") for a in lecturers[lid].availability)
  sid, *ids = cases["pula-za-maly-limit"]["ids"]
  pool_ids = [i for i in ids if i in lecturers]
  assert sum(lecturers[i].max_units_weekly for i in pool_ids) < expected_week_units([c for c in ds.classes if c.id in ids])


@pytest.mark.parametrize("name", CLEAN)
def test_lecturer_pools_cover_modes_and_demand(results, name):
  """Typowa pula: kompetencja, osoba na każdy tryb studiów i łączny limit ≥ zapotrzebowanie formy."""
  ds = results[name].dataset
  lecturers = {lec.id: lec for lec in ds.lecturers}
  mode = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: mode[g.program_id] for g in ds.groups}
  pooled = [s for s in ds.subjects if s.lecturer_pools]
  assert pooled
  if len(ds.subjects) >= 20:
    assert len(pooled) < len(ds.subjects)  # część przedmiotów bez puli (pool_share)
  for s in pooled:
    for form, pool in s.lecturer_pools.items():
      cs = [c for c in ds.classes if c.subject_id == s.id and c.form == form]
      assert cs, (s.id, form)
      members = [lecturers[i] for i in pool]
      assert all(s.competence in m.competences and m.availability for m in members)
      for m in {gmode[c.group_id] for c in cs}:
        weekend = m == "part-time"
        assert any(any((a.day in ("So", "Nd")) == weekend for a in x.availability) for x in members), (s.id, form, m)
      slack = load_preset(name).lecturers.slack
      assert sum(x.max_units_weekly for x in members) >= expected_week_units(cs) * slack, (s.id, form)
      for m in {gmode[c.group_id] for c in cs}:
        in_mode = [c for c in cs if gmode[c.group_id] == m]
        minutes = max(sum(c.meetings_per_week * meeting_minutes(c.units_per_meeting, ds.config.time_grid)
                          for c in in_mode if c.parity in ("every", w)) for w in ("A", "B"))
        assert sum(available_minutes(x, m, ds.config) for x in members) >= minutes * slack, (s.id, form, m)
        meeting = max(meeting_minutes(c.units_per_meeting, ds.config.time_grid) for c in in_mode)
        days = ds.config.time_grid.days_by_study_mode[m]
        able = [x for x in members if any(fits((to_minutes(a.from_), to_minutes(a.to)), meeting, ds.config.time_grid)
                                          for a in x.availability if a.day in days)]
        assert able, (s.id, form, m)  # ktoś zmieści spotkanie
        assert sum(x.max_units_weekly for x in able) >= expected_week_units(in_mode) * slack, (s.id, form, m)


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


@pytest.mark.parametrize("name", CLEAN)
def test_competence_limits_cover_peak_demand(results, name):
  """Rzeczywiście wylosowane limity (nie szacunek) pokrywają zapotrzebowanie w gorszym tygodniu × slack."""
  ds = results[name].dataset
  slack = load_preset(name).lecturers.slack
  mode = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: mode[g.program_id] for g in ds.groups}
  comp = {s.id: s.competence for s in ds.subjects}
  for k in set(comp.values()):
    cs = [c for c in ds.classes if comp[c.subject_id] == k]
    staff = [lec for lec in ds.lecturers if k in lec.competences]
    need = {m: expected_week_units([c for c in cs if gmode[c.group_id] == m]) for m in {gmode[c.group_id] for c in cs}}
    usable = [lec for lec in staff if any(available_minutes(lec, m, ds.config) for m in ("full-time", "part-time"))]
    assert sum(lec.max_units_weekly for lec in usable) >= sum(need.values()) * slack, k
    for m, units in need.items():
      in_mode = [lec for lec in staff if available_minutes(lec, m, ds.config)]
      assert sum(lec.max_units_weekly for lec in in_mode) >= units * slack, (k, m)


def test_zero_availability_draws_do_not_stop_staffing():
  """Wzorzec, który czasem losuje 0 dni, nie zatrzymuje dobierania prowadzących."""
  preset = load_preset("kierunek")
  for p in preset.lecturers.patterns.values():
    p.days = (0, p.days[1])
  ds = generate(preset).dataset
  assert reference_errors(ds) == []
  from krok.perception import analyze
  assert analyze(ds)[1].verdict == "no-obstacles-found"


def test_impossible_lecturer_preset_is_reported():
  preset = load_preset("mini")
  for p in preset.lecturers.patterns.values():
    p.days = (0, 0)
  preset.lecturers.external_days = (0, 0)
  with pytest.raises(ValueError, match="Nie da się dobrać prowadzących"):
    generate(preset)


def test_one_lecturer_can_cover_both_study_modes_in_pool():
  """Jedyna osoba z kompetencją, dostępna w dni robocze i w weekend, tworzy pulę dla formy z zajęciami w obu trybach."""
  import random
  from krok.generator.pools import build_pools
  from krok.model import Availability
  preset = load_preset("kierunek")
  preset.lecturers.pool_share = 1.0
  ds = generate(preset).dataset
  mode = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: mode[g.program_id] for g in ds.groups}
  subject, form = next((s, f) for s in ds.subjects for f in ("W", "C", "L", "P")
                       if {gmode[c.group_id] for c in ds.classes if c.subject_id == s.id and c.form == f}
                       == {"full-time", "part-time"})
  only = ds.lecturers[0]
  only.competences = [subject.competence]
  only.availability = [Availability(day=d, from_="07:30", to="20:00") for d in ("Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd")]
  only.max_units_weekly = 200
  others = [lec.model_copy(update={"competences": [c for c in lec.competences if c != subject.competence] or ["inna"]})
            for lec in ds.lecturers[1:]]
  classes = [c for c in ds.classes if c.subject_id == subject.id]
  subject.lecturer_pools = None
  build_pools(preset, ds.config, [subject], classes, [only, *others], ds.groups, ds.programs, random.Random(1))
  assert subject.pool(form) == [only.id]


@pytest.mark.parametrize("name, seed", [("wydzial", 35), ("kierunek", 42), ("wydzial", 42)])
def test_overlapping_pools_fit_members_limits_together(name, seed):
  """Pule dzielą ludzi: suma zapotrzebowań pul jednej kompetencji i trybu mieści się w limitach ich członków.

  Regresja: wydzial/35 miał pule SK i SRW z tymi samymi osobami, które razem potrzebowały 62 jednostek przy limicie 61.
  """
  from krok.perception import analyze
  ds = generate(load_preset(name), seed).dataset
  assert analyze(ds)[1].errors == 0
  mode = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: mode[g.program_id] for g in ds.groups}
  limit = {lec.id: lec.max_units_weekly for lec in ds.lecturers}
  by_key = defaultdict(lambda: (set(), []))
  for s in ds.subjects:
    for form, pool in (s.lecturer_pools or {}).items():
      for c in ds.classes:
        if c.subject_id == s.id and c.form == form:
          members, classes = by_key[(s.competence, gmode[c.group_id])]
          members.update(pool)
          classes.append(c)
  for key, (members, classes) in by_key.items():
    assert sum(limit[m] for m in members) >= expected_week_units(classes), key
