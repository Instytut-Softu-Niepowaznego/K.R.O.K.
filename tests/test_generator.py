"""Generator (#7). Czy dane da się ułożyć, sprawdza raport wykonalności w test_perception.py."""
import pytest
from pydantic import ValidationError

from krok.generator import generate, load_preset, write
from krok.generator.core import load_generated
from krok.model import dataset_from_files, reference_errors


@pytest.mark.parametrize("name", ["mini", "kierunek", "wydzial", "edge-cases"])
def test_preset_is_consistent_and_roundtrips(name, tmp_path):
  r = generate(load_preset(name))
  assert reference_errors(r.dataset) == []
  write(r, tmp_path)
  assert load_generated(tmp_path) == r.dataset


def test_edge_cases_are_all_injected_and_point_to_real_ids():
  r = generate(load_preset("edge-cases"))
  ds = r.dataset
  cases = {c["case"]: c for c in r.meta["edgeCases"]}
  assert set(cases) == {"grupa-wieksza-od-sal", "przedmiot-bez-prowadzacego", "prowadzacy-bez-dostepnosci",
                        "limit-ponizej-obciazenia", "zajecia-1-jednostkowe", "zajecia-ab", "prowadzacy-dzienne-i-zaoczne",
                        "zajecia-dluzsze-niz-dostepnosc", "pula-niedostepna", "pula-za-maly-limit",
                        "nakladajace-sie-wyjatki", "odpracowanie-w-dniu-wolnym"}
  known = {x.id for coll in (ds.groups, ds.lecturers, ds.subjects, ds.classes, ds.calendar.exceptions) for x in coll}
  for c in cases.values():
    assert set(c["ids"]) <= known, c["case"]


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
