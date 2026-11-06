import json

import pytest

from krok.generator import generate, load_preset
from krok.model import Building, ResourceBlock, Scope
from krok.perception import analyze, build_state, meeting_minutes, travel_matrix, travel_minutes
from krok.perception.cli import main
from krok.perception.measures import fits, merge, subtract


def data(name: str = "mini"):
  return generate(load_preset(name)).dataset


def codes(report, severity="error") -> set[str]:
  return {p.code for p in report.problems if p.severity == severity}


def errors_with(report, code: str):
  return [p for p in report.problems if p.code == code and p.severity == "error"]


def full_block(ds, bid: str, **scope) -> ResourceBlock:
  return ResourceBlock(id=bid, type="resourceBlock", from_=ds.calendar.semester_start, to=ds.calendar.semester_end,
                       scope=Scope(**scope))


# --- miary ---

def test_travel_minutes_formula_from_criteria():
  """Przykład z kryteriów: 0,4 km × 1,3 / 5 km/h × 60 ≈ 6 min."""
  ds = data()
  a = Building(id="A", name="A", lat=50.0, lon=17.9, faculty_ids=["WJ"])
  b = Building(id="B", name="B", lat=50.0 + 0.4 / 111.195, lon=17.9, faculty_ids=["WJ"])
  assert travel_minutes(a, b, ds.config) == pytest.approx(0.4 * 1.3 / 5 * 60, abs=0.01)
  m = travel_matrix(ds.buildings, ds.config)
  for x in m:
    assert m[x][x] == 0
    for y in m:
      assert m[x][y] == m[y][x] >= 0


def test_meeting_minutes_uses_shortest_inner_break():
  grid = data().config.time_grid  # innerMinutes 10, override 16–17 z innerMinutes 5
  assert meeting_minutes(1, grid) == 45
  assert meeting_minutes(2, grid) == 95
  grid.breaks.overrides = []
  assert meeting_minutes(2, grid) == 100


def test_fits_respects_start_step_and_allowed_starts():
  grid = data().config.time_grid  # dayStart 07:30, krok 5 min
  assert fits((451, 550), 95, grid)  # pierwszy start 07:35, koniec 09:10
  assert not fits((451, 549), 95, grid)
  grid.allowed_start_times = ["08:20"]
  assert fits((480, 600), 95, grid) and not fits((480, 590), 95, grid)


def test_interval_helpers():
  assert merge([(5, 10), (0, 3), (2, 6)]) == [(0, 10)]
  assert subtract([(0, 10)], (3, 5)) == [(0, 3), (5, 10)]
  assert subtract([(0, 10)], (0, 10)) == []


# --- stan otoczenia ---

def test_state_matrices_and_rooms_grouping():
  ds = data("kierunek")
  state = build_state(ds)
  assert set(state.travel) == {b.id for b in ds.buildings}
  lec = next(lec for lec in ds.lecturers if lec.availability)
  assert sum(e - s for days in [state.lecturer_availability[lec.id]] for iv in days.values() for s, e in iv) > 0
  for room_type, rooms in state.rooms_by_type.items():
    assert [r.capacity for r in rooms] == sorted((r.capacity for r in rooms), reverse=True)
    assert all(r.type == room_type for r in rooms)
  assert sum(sum(c.values()) for c in state.rooms_by_capacity().values()) == len(ds.rooms)
  exported = json.loads(json.dumps(state.export()))
  assert exported["lecturerAvailability"][lec.id]
  assert set(exported["classes"]) == {c.id for c in ds.classes}


def test_full_semester_blocks_remove_resources():
  ds = data()
  lab_rooms = [r.id for r in ds.rooms if r.type == "lab-it"]
  lec = next(lec for lec in ds.lecturers if lec.availability)
  ds.calendar.exceptions += [full_block(ds, "b1", room_ids=lab_rooms), full_block(ds, "b2", lecturer_ids=[lec.id])]
  short = ResourceBlock(id="b3", type="resourceBlock", from_=ds.calendar.semester_start, to=ds.calendar.semester_start,
                        scope=Scope(room_ids=[r.id for r in ds.rooms]))
  ds.calendar.exceptions.append(short)  # krótka blokada nie zmienia tygodniowego planu
  state, report = analyze(ds)
  assert "lab-it" not in state.rooms_by_type and state.lecturer_availability[lec.id] == {}
  assert any("lab-it" in p.message for p in errors_with(report, "brak-sali"))
  assert len(state.rooms_by_type["lecture-hall"]) == sum(r.type == "lecture-hall" for r in ds.rooms)


def test_partial_full_semester_block_cuts_hours():
  ds = data()
  lec = next(lec for lec in ds.lecturers if lec.availability)
  block = full_block(ds, "b", lecturer_ids=[lec.id])
  block.from_time, block.to_time = "07:30", "20:00"
  ds.calendar.exceptions.append(block)
  assert build_state(ds).lecturer_availability[lec.id] == {}


# --- raport: presety ---

@pytest.mark.parametrize("name", ["mini", "kierunek", "wydzial"])
def test_typical_presets_have_no_obstacles(name):
  _, report = analyze(data(name))
  assert report.verdict == "no-obstacles-found" and report.errors == 0, [p.message for p in report.problems]


def test_wydzial_performance():
  ds = data("wydzial")
  _, report = analyze(ds)
  assert report.compute_seconds < 2
  assert report.counts["classes"] == len(ds.classes)


def test_edge_cases_are_detected():
  """Każdy przypadek brzegowy, który jest przeszkodą, trafia do raportu z właściwą regułą i id."""
  r = generate(load_preset("edge-cases"))
  cases = {c["case"]: c["ids"] for c in r.meta["edgeCases"]}
  _, report = analyze(r.dataset)
  assert report.verdict == "infeasible"

  def found(code: str, rule: str, ids: list[str]) -> bool:
    return any(p.rule_id == rule and set(ids) & (set(p.ids) | set(p.class_ids)) for p in errors_with(report, code))

  assert found("brak-sali", "pojemnosc-sali", [cases["grupa-wieksza-od-sal"][0]])
  assert found("brak-kompetencji", "kompetencja-prowadzacego", cases["przedmiot-bez-prowadzacego"])
  assert found("brak-dostepnego-prowadzacego", "dostepnosc-prowadzacego", cases["prowadzacy-bez-dostepnosci"])
  assert found("limit-prowadzacego", "limit-godzin-prowadzacego", cases["limit-ponizej-obciazenia"][:1])
  assert found("brak-dostepnego-prowadzacego", "dostepnosc-prowadzacego", cases["zajecia-dluzsze-niz-dostepnosc"])
  assert found("brak-dostepnego-prowadzacego", "dostepnosc-prowadzacego", cases["pula-niedostepna"][1:2])
  assert found("limit-puli", "limit-godzin-prowadzacego", cases["pula-za-maly-limit"])
  assert any(set(cases["odpracowanie-w-dniu-wolnym"]) <= set(p.ids) for p in report.problems if p.code == "odpracowanie-w-dniu-wolnym")

  # przypadki, które nie są przeszkodą, nie dają błędów (poza brakiem sali dla celowo powiększonej grupy)
  big = cases["grupa-wieksza-od-sal"][0]
  flagged = {cid for p in report.problems if p.severity == "error" and big not in p.ids for cid in p.class_ids}
  for case in ("zajecia-1-jednostkowe", "zajecia-ab", "prowadzacy-dzienne-i-zaoczne"):
    assert not set(cases[case]) & flagged, case


# --- raport: każde sprawdzenie na zepsutych danych ---

def test_room_time_shortage():
  ds = data()
  keep = next(r for r in ds.rooms if r.type == "lab-it")
  ds.rooms = [r for r in ds.rooms if r.type != "lab-it" or r is keep]
  ds.config.time_grid.days_by_study_mode["full-time"] = ["Pn"]
  _, report = analyze(ds)
  assert {"za-malo-sal", "za-malo-czasu-grupy"} <= codes(report)
  assert all(p.rule_id == "komplet-godzin" for p in errors_with(report, "za-malo-sal"))


def test_daily_unit_limit():
  ds = data()
  next(r for r in ds.config.rules if r.id == "max-jednostek-dziennie").params["maxUnits"] = 1
  _, report = analyze(ds)
  assert {"spotkanie-ponad-limit-dzienny", "za-duzo-jednostek-grupy"} <= codes(report)


def test_meeting_longer_than_day():
  ds = data()
  ds.config.time_grid.day_end = "08:30"
  _, report = analyze(ds)
  assert "zajecia-dluzsze-niz-dzien" in codes(report)


def test_competence_limit():
  ds = data()
  for lec in ds.lecturers:
    lec.max_units_weekly = 1
  _, report = analyze(ds)
  assert "limit-kompetencji" in codes(report)


def test_blocked_group():
  ds = data()
  cohort = next(g for g in ds.groups if g.kind == "cohort")
  ds.calendar.exceptions.append(full_block(ds, "g", group_ids=[cohort.id]))
  _, report = analyze(ds)
  blocked = errors_with(report, "zablokowana-grupa")
  assert len(blocked) == len(ds.classes)  # blokada rocznika obejmuje wszystkie podgrupy


def test_disabled_rules_turn_errors_into_warnings():
  ds = generate(load_preset("edge-cases")).dataset
  next(r for r in ds.config.rules if r.id == "pojemnosc-sali").enabled = False
  _, report = analyze(ds)
  assert "brak-sali" not in codes(report) and "brak-sali" in codes(report, "warning")


def test_without_room_type_rule_any_room_counts():
  ds = generate(load_preset("edge-cases")).dataset
  next(r for r in ds.config.rules if r.id == "typ-sali").enabled = False
  _, report = analyze(ds)
  assert "brak-sali" not in codes(report) | codes(report, "warning")  # grupa 20 os. mieści się w sali wykładowej


def _two_hours():
  from krok.model import Availability
  return [Availability(day="Pn", from_="08:00", to="10:00")]  # mieści jedno spotkanie 95 min, ale nie więcej


def test_competence_time_shortage():
  ds = data("kierunek")
  comp = "programowanie"
  for lec in ds.lecturers:
    if comp in lec.competences:
      lec.availability = _two_hours()
  _, report = analyze(ds)
  assert any(comp in p.ids for p in errors_with(report, "za-malo-czasu-kompetencji"))


def test_pool_time_shortage():
  """Pula z oknem na jedno spotkanie, a zajęć jest więcej: za-malo-czasu-puli."""
  from krok.model import Availability, to_hhmm
  ds = data("kierunek")
  modes = {p.id: p.study_mode for p in ds.programs}
  gmode = {g.id: modes[g.program_id] for g in ds.groups}
  subject, form, pool = next(
    (s, f, p) for s in ds.subjects for f, p in (s.lecturer_pools or {}).items()
    if len(cs := [c for c in ds.classes if c.subject_id == s.id and c.form == f]) >= 2
    and all(gmode[c.group_id] == "full-time" and c.parity == "every" for c in cs))
  units = next(c.units_per_meeting for c in ds.classes if c.subject_id == subject.id and c.form == form)
  for lec in ds.lecturers:
    if lec.id in pool:
      lec.availability = []
  first = next(lec for lec in ds.lecturers if lec.id == pool[0])
  first.availability = [Availability(day="Pn", from_="08:00", to=to_hhmm(480 + meeting_minutes(units, ds.config.time_grid)))]
  _, report = analyze(ds)
  assert any(subject.id in p.ids for p in errors_with(report, "za-malo-czasu-puli")), (subject.id, form)


def test_pinned_lecturer_without_competence():
  ds = data()
  c = ds.classes[0]
  subject = next(s for s in ds.subjects if s.id == c.subject_id)
  outsider = next(lec for lec in ds.lecturers if subject.competence not in lec.competences)
  subject.lecturer_pools = None
  c.lecturer_id = outsider.id
  _, report = analyze(ds)
  assert c.id in {cid for p in errors_with(report, "przydzial-bez-kompetencji") for cid in p.class_ids}


def test_disabled_complete_hours_means_no_obstacles():
  """Bez komplet-godzin plan może pominąć zajęcia, więc żaden problem z zajęciami nie jest przeszkodą."""
  ds = generate(load_preset("edge-cases")).dataset
  next(r for r in ds.config.rules if r.id == "komplet-godzin").enabled = False
  _, report = analyze(ds)
  assert report.verdict == "no-obstacles-found" and report.warnings >= 9


def test_lecturer_without_availability_is_warning():
  ds = data()
  ds.lecturers[0].availability = []
  _, report = analyze(ds)
  assert ds.lecturers[0].id in {i for p in report.problems if p.code == "prowadzacy-bez-dostepnosci" for i in p.ids}


# --- CLI ---

def test_cli_exit_codes_and_files(tmp_path, capsys):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "kierunek", "--out", str(tmp_path / "ok"), "-q"]) == 0
  assert main(["--data", str(tmp_path / "ok")]) == 0
  report = json.loads((tmp_path / "ok" / "_feasibility.json").read_text())
  assert report["verdict"] == "no-obstacles-found" and "travelMinutes" in report
  assert (tmp_path / "ok" / "_environment.json").exists()

  assert main(["--preset", "edge-cases", "--out", str(tmp_path / "edge"), "-q"]) == 1
  assert "PLANU NIE DA SIĘ UŁOŻYĆ" not in capsys.readouterr().out  # -q
  assert main(["--preset", "edge-cases"]) == 1 and "PLANU NIE DA SIĘ UŁOŻYĆ" in capsys.readouterr().out

  assert gen_main(["--preset", "mini", "--noise", "--out", str(tmp_path / "noisy"), "-q"]) == 0
  assert main(["--data", str(tmp_path / "noisy")]) == 2  # dane z szumem: najpierw #9
  assert main(["--data", str(tmp_path / "brak")]) == 2
  assert main(["--preset", "nie-ma"]) == 2


def test_cli_rejects_dangling_references(tmp_path):
  from krok.generator.cli import main as gen_main
  assert gen_main(["--preset", "mini", "--out", str(tmp_path), "-q"]) == 0
  classes = json.loads((tmp_path / "classes.json").read_text())
  classes[0]["groupId"] = "NIE-MA"
  (tmp_path / "classes.json").write_text(json.dumps(classes))
  assert main(["--data", str(tmp_path)]) == 2



EXPECTED = {"grupa-wieksza-od-sal": "brak-sali", "przedmiot-bez-prowadzacego": "brak-kompetencji",
            "prowadzacy-bez-dostepnosci": "brak-dostepnego-prowadzacego", "limit-ponizej-obciazenia": "limit-prowadzacego",
            "zajecia-dluzsze-niz-dostepnosc": "brak-dostepnego-prowadzacego",
            "pula-niedostepna": "brak-dostepnego-prowadzacego", "pula-za-maly-limit": "limit-puli",
            "odpracowanie-w-dniu-wolnym": "odpracowanie-w-dniu-wolnym"}


def test_edge_cases_detected_across_seeds():
  """Na każdym ziarnie każdy przypadek brzegowy będący przeszkodą trafia do raportu."""
  preset = load_preset("edge-cases")
  for seed in range(40):
    r = generate(preset, seed)
    _, report = analyze(r.dataset)
    for case in r.meta["edgeCases"]:
      code = EXPECTED.get(case["case"])
      if code:
        ids = set(case["ids"])
        assert any(p.code == code and ids & (set(p.ids) | set(p.class_ids)) for p in report.problems), (seed, case)


def test_clean_presets_have_no_errors_across_seeds():
  for name in ("mini", "kierunek"):
    preset = load_preset(name)
    for seed in range(15):
      _, report = analyze(generate(preset, seed).dataset)
      assert report.errors == 0, (name, seed, [p.message for p in report.problems if p.severity == "error"])
