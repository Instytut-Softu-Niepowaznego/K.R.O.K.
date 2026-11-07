"""Stan otoczenia i raport wykonalności (#10)."""
import pytest

from krok.generator import generate, load_preset
from krok.model import Availability, Building, ResourceBlock, Scope
from krok.perception import analyze, travel_minutes


def data(name: str = "mini"):
  """Czyste dane presetu."""
  return generate(load_preset(name)).dataset


def codes(report, severity="error") -> set[str]:
  """Kody problemów o danej wadze z raportu."""
  return {p.code for p in report.problems if p.severity == severity}


def full_block(ds, bid: str, **scope) -> ResourceBlock:
  """Blokada zasobów na cały semestr."""
  return ResourceBlock(id=bid, type="resourceBlock", from_=ds.calendar.semester_start, to=ds.calendar.semester_end,
                       scope=Scope(**scope))


def rule(ds, rule_id: str):
  """Reguła z konfiguracji o podanym id."""
  return next(r for r in ds.config.rules if r.id == rule_id)


def test_travel_minutes_formula_from_criteria():
  """Przykład z kryteriów: 0,4 km × 1,3 / 5 km/h × 60 ≈ 6 min."""
  ds = data()
  a = Building(id="A", name="A", lat=50.0, lon=17.9, faculty_ids=["WJ"])
  b = Building(id="B", name="B", lat=50.0 + 0.4 / 111.195, lon=17.9, faculty_ids=["WJ"])
  assert travel_minutes(a, b, ds.config) == pytest.approx(0.4 * 1.3 / 5 * 60, abs=0.01)


def test_clean_presets_have_no_obstacles_across_seeds():
  """Typowe dane z generatora da się ułożyć. wydzial/35 to regresja: dwie pule z tymi samymi osobami przekraczały limit."""
  runs = [("mini", s) for s in range(15)] + [("kierunek", s) for s in range(15)] + [("wydzial", s) for s in (None, 35, 42)]
  for name, seed in runs:
    _, report = analyze(generate(load_preset(name), seed).dataset)
    assert report.errors == 0, (name, seed, [p.message for p in report.problems if p.severity == "error"])


# Przypadek brzegowy, który jest przeszkodą → kod problemu w raporcie.
EXPECTED = {"grupa-wieksza-od-sal": "brak-sali", "przedmiot-bez-prowadzacego": "brak-kompetencji",
            "prowadzacy-bez-dostepnosci": "brak-dostepnego-prowadzacego", "limit-ponizej-obciazenia": "limit-prowadzacego",
            "zajecia-dluzsze-niz-dostepnosc": "brak-dostepnego-prowadzacego",
            "pula-niedostepna": "brak-dostepnego-prowadzacego", "pula-za-maly-limit": "limit-puli",
            "odpracowanie-w-dniu-wolnym": "odpracowanie-w-dniu-wolnym"}
NOT_OBSTACLES = ("zajecia-1-jednostkowe", "zajecia-ab", "prowadzacy-dzienne-i-zaoczne")


def test_edge_cases_are_detected_across_seeds():
  """Na każdym ziarnie każdy przypadek będący przeszkodą trafia do raportu, a pozostałe nie dają błędów."""
  preset = load_preset("edge-cases")
  for seed in range(40):
    r = generate(preset, seed)
    _, report = analyze(r.dataset)
    assert report.verdict == "infeasible", seed
    for case in r.meta["edgeCases"]:
      if code := EXPECTED.get(case["case"]):
        ids = set(case["ids"])
        assert any(p.code == code and ids & (set(p.ids) | set(p.class_ids)) for p in report.problems), (seed, case)
    big = next(c["ids"][0] for c in r.meta["edgeCases"] if c["case"] == "grupa-wieksza-od-sal")
    flagged = {cid for p in report.problems if p.severity == "error" and big not in p.ids for cid in p.class_ids}
    for case in r.meta["edgeCases"]:
      if case["case"] in NOT_OBSTACLES:
        assert not set(case["ids"]) & flagged, (seed, case)


def _few_lab_rooms(ds):
  """Jedna sala lab-it i zajęcia dzienne tylko w poniedziałek."""
  keep = next(r for r in ds.rooms if r.type == "lab-it")
  ds.rooms = [r for r in ds.rooms if r.type != "lab-it" or r is keep]
  ds.config.time_grid.days_by_study_mode["full-time"] = ["Pn"]


def _daily_limit(ds):
  """Limit 1 jednostki dziennie."""
  rule(ds, "max-jednostek-dziennie").params["maxUnits"] = 1


def _short_day(ds):
  """Dzień zajęć kończy się o 08:30."""
  ds.config.time_grid.day_end = "08:30"


def _low_limits(ds):
  """Każdy prowadzący ma limit 1 jednostki tygodniowo."""
  for lec in ds.lecturers:
    lec.max_units_weekly = 1


def _blocked_cohort(ds):
  """Rocznik zablokowany na cały semestr."""
  cohort = next(g for g in ds.groups if g.kind == "cohort")
  ds.calendar.exceptions.append(full_block(ds, "g", group_ids=[cohort.id]))


def _pinned_outsider(ds):
  """Zajęcia przypisane prowadzącemu bez kompetencji przedmiotu."""
  c = ds.classes[0]
  subject = next(s for s in ds.subjects if s.id == c.subject_id)
  subject.lecturer_pools = None
  c.lecturer_id = next(lec for lec in ds.lecturers if subject.competence not in lec.competences).id


def _competence_two_hours(ds):
  """Prowadzący z kompetencją programowanie mają tylko 2 godziny w tygodniu."""
  for lec in ds.lecturers:
    if "programowanie" in lec.competences:
      lec.availability = [Availability(day="Pn", from_="08:00", to="10:00")]  # jedno spotkanie 95 min, nie więcej


@pytest.mark.parametrize("preset, broken, code", [
  ("mini", _few_lab_rooms, "za-malo-sal"),
  ("mini", _daily_limit, "spotkanie-ponad-limit-dzienny"),
  ("mini", _short_day, "zajecia-dluzsze-niz-dzien"),
  ("mini", _low_limits, "limit-kompetencji"),
  ("mini", _blocked_cohort, "zablokowana-grupa"),
  ("mini", _pinned_outsider, "przydzial-bez-kompetencji"),
  ("kierunek", _competence_two_hours, "za-malo-czasu-kompetencji"),
], ids=lambda x: x.__name__.strip("_") if callable(x) else None)
def test_each_obstacle_is_detected(preset, broken, code):
  """Celowo zepsute dane dają w raporcie właściwy kod przeszkody."""
  ds = data(preset)
  broken(ds)
  _, report = analyze(ds)
  assert code in codes(report), sorted(codes(report))


def test_rules_from_config_switch_checks():
  """Wyłączona reguła w konfiguracji przestaje być przeszkodą."""
  ds = data("edge-cases")
  rule(ds, "pojemnosc-sali").enabled = False
  _, report = analyze(ds)
  assert "brak-sali" not in codes(report) and "brak-sali" in codes(report, "warning")

  ds = data("edge-cases")
  rule(ds, "komplet-godzin").enabled = False  # plan może pominąć zajęcia
  assert analyze(ds)[1].verdict == "no-obstacles-found"

  ds = data()
  ds.calendar.exceptions.append(full_block(ds, "w", room_ids=[r.id for r in ds.rooms]))
  assert analyze(ds)[1].verdict == "infeasible"
  rule(ds, "blokady-zasobow").enabled = False
  assert analyze(ds)[1].verdict == "no-obstacles-found"
