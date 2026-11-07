"""Odporność CLI na złe wejście i wyjście: każdy problem kończy się komunikatem i kodem 2, a nie tracebackiem.

Przypadki pochodzą z przeglądu po recenzjach CodeRabbit do #33 (uprząż: każde CLI × każdy plik × zepsuty wariant).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from krok.events.cli import main as events_main
from krok.generator.cli import main as gen_main
from krok.model import DatasetError, dataset_from_files, read_files
from krok.perception.cli import main as env_main
from krok.perception.quality_cli import main as quality_main

CORRUPT = {"directory": None, "empty": b"", "bad-utf8": b"\xff\xfe", "bad-json": b"{zly", "obj": b"{}", "list": b"[]",
           "null": b"null", "text": b'"tekst"', "list-of-null": b"[null]"}


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
  out = tmp_path_factory.mktemp("dane")
  assert events_main(["generate", "--preset", "mini", "--out", str(out), "-q"]) == 0
  return out


def corrupted(base: Path, tmp_path: Path, name: str, how: str) -> Path:
  d = tmp_path / "dane"
  shutil.copytree(base, d)
  (d / name).unlink()
  if CORRUPT[how] is None:
    (d / name).mkdir()
  else:
    (d / name).write_bytes(CORRUPT[how])
  return d


@pytest.mark.parametrize("how", CORRUPT)
@pytest.mark.parametrize("name", ["calendar.json", "faculties.json", "config.json", "groups.json", "rooms.json"])
def test_corrupted_data_file_is_exit_2(base, tmp_path, capsys, name, how):
  if how == "list" and name in ("groups.json", "rooms.json"):
    pytest.skip("pusta lista rekordów to poprawne dane (np. brak sal dopuszcza #6)")
  d = corrupted(base, tmp_path, name, how)
  assert env_main(["--data", str(d), "-q"]) == 2
  assert events_main(["generate", "--data", str(d), "-q"]) == 2
  err = capsys.readouterr().err
  assert name in err or "walidacji" in err, err  # komunikat wskazuje plik albo błąd walidacji rekordów


def test_corrupted_meta_is_exit_2_but_missing_meta_is_fine(base, tmp_path):
  d = corrupted(base, tmp_path, "_meta.json", "bad-json")
  assert events_main(["generate", "--data", str(d), "-q"]) == 2
  (d / "_meta.json").write_text('{"preset": 5}')
  assert events_main(["generate", "--data", str(d), "-q"]) == 2
  (d / "_meta.json").unlink()
  assert events_main(["generate", "--data", str(d), "--preset", "mini", "-q"]) == 0


@pytest.mark.parametrize("files, message", [
  ({"calendar.json": {}}, 'w polu "calendar"'),
  ({"calendar.json": []}, "calendar.json"),
  ({"faculties.json": "x"}, "faculties.json"),
  ({"faculties.json": {"faculties": [], "fields": [], "programs": {}}}, "faculties.json/programs"),
  ({"groups.json": {}}, "groups.json"),
  ({"config.json": []}, "config.json"),
])
def test_dataset_shape_errors_name_the_file(base, files, message):
  data = read_files(base)
  data.update(files)
  with pytest.raises(DatasetError, match=message):
    dataset_from_files(data)


@pytest.mark.parametrize("option", ["--rooms", "--buildings"])
@pytest.mark.parametrize("content", [None, b"{zly", b"\xff", b"null", b"{}", b'"x"', b"[1]", b'[{"id": "P1"}]'])
def test_generator_base_data_errors_are_exit_2(tmp_path, option, content):
  f = tmp_path / "plik.json"
  if content is not None:
    f.write_bytes(content)
  assert gen_main(["--preset", "mini", option, str(f), "--out", str(tmp_path / "o"), "-q"]) == 2


def test_generator_rejects_empty_buildings(tmp_path, capsys):
  (tmp_path / "b.json").write_text("[]")
  assert gen_main(["--preset", "mini", "--buildings", str(tmp_path / "b.json"), "--out", str(tmp_path / "o"), "-q"]) == 2
  assert "brak budynków" in capsys.readouterr().err


@pytest.mark.parametrize("content", [b"name = \n[[", b'name = "x"\n', b"\xff\xfe", b'name = 1\nfields = "x"\n',
                                     b'name = "x"\nfields = []\n', b'name = "x"\nfoo = 1\n[[fields]]\nid = "A"\nname = "A"\n'])
def test_bad_preset_is_exit_2_everywhere(tmp_path, content):
  preset = tmp_path / "p.toml"
  preset.write_bytes(content)
  out = str(tmp_path / "o")
  assert gen_main(["--preset", str(preset), "--out", out, "-q"]) == 2
  assert env_main(["--preset", str(preset), "-q"]) == 2
  assert events_main(["generate", "--preset", str(preset), "--out", out, "-q"]) == 2


def test_extends_cycle_is_reported(tmp_path, capsys):
  # json.dumps daje poprawny napis TOML także dla ścieżek z Windows (C:\Users → "C:\\Users"), w których \U i \b
  # byłyby w TOML sekwencjami ucieczki.
  (tmp_path / "a.toml").write_text(f'extends = {json.dumps(str(tmp_path / "b.toml"), ensure_ascii=False)}\nname = "a"\n')
  (tmp_path / "b.toml").write_text(f'extends = {json.dumps(str(tmp_path / "a.toml"), ensure_ascii=False)}\nname = "b"\n')
  assert gen_main(["--preset", str(tmp_path / "a.toml"), "-q"]) == 2
  assert "Cykl w extends" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e309", "0", "-1", "100001"])
def test_events_per_week_must_be_finite_and_bounded(tmp_path, value):
  """--per-week inf albo 1e309 zawieszało generator na zawsze (odstępy między zdarzeniami = 0)."""
  assert events_main(["generate", "--preset", "mini", f"--per-week={value}", "--out", str(tmp_path), "-q"]) == 2


def test_events_per_week_limit_also_in_library():
  from krok.events import generate_events
  from krok.generator import generate, load_preset
  preset = load_preset("mini")
  preset.events.per_week = float("inf")  # przypisanie omija walidację presetu
  with pytest.raises(ValueError, match="per_week"):
    generate_events(generate(preset).dataset, preset, 1)


@pytest.mark.parametrize("value, code", [("nan", 2), ("-1", 2), ("0", 0), ("inf", 0)])
def test_replay_speed(base, value, code):
  assert events_main(["replay", str(base / "events.jsonl"), f"--speed={value}", "--errors-only"]) == code


def test_replay_speed_check_in_library(base):
  from krok.events import replay
  with pytest.raises(ValueError, match="speed"):
    next(replay(base / "events.jsonl", speed=-1))


@pytest.mark.parametrize("target", ["file", "readonly"])
def test_unwritable_out_is_exit_2(base, tmp_path, target):
  if target == "file":
    out = tmp_path / "plik"
    out.write_text("x")
  else:
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    out = ro / "x"
    probe = ro / "probe"
    try:
      probe.write_text("x")  # root ignoruje prawa dostępu: wtedy tego wariantu nie da się sprawdzić
      pytest.skip("brak ograniczeń praw zapisu (np. root)")
    except PermissionError:
      pass
  try:
    assert gen_main(["--preset", "mini", "--out", str(out), "-q"]) == 2
    assert env_main(["--data", str(base), "--out", str(out), "-q"]) == 2
    assert events_main(["generate", "--preset", "mini", "--out", str(out), "-q"]) == 2
    assert quality_main(["--data", str(base), "--out", str(out), "-q"]) == 2
  finally:
    if target == "readonly":
      (tmp_path / "ro").chmod(0o755)


@pytest.mark.parametrize("args", [
  ["krok.generator.cli", "--preset", "wydzial", "--out", "{tmp}/o"],
  ["krok.generator.cli", "--list"],
  ["krok.perception.cli", "--data", "{base}"],
  ["krok.perception.quality_cli", "--data", "{base}", "--out", "{tmp}/q"],
])
def test_closed_pipe_exits_quietly(base, tmp_path, args):
  """`krok-… | head -0`: bez kodu 120 i bez „Exception ignored … BrokenPipeError”."""
  argv = [a.replace("{tmp}", str(tmp_path)).replace("{base}", str(base)) for a in args]
  proc = subprocess.Popen([sys.executable, "-m", *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  proc.stdout.close()
  err = proc.stderr.read()
  assert proc.wait() in (0, 1)
  assert b"Traceback" not in err and b"Exception ignored" not in err, err[-300:]


def test_closed_pipe_safe_keeps_exit_code(capsys):
  from krok.console import closed_pipe_safe

  @closed_pipe_safe
  def ok():
    return 1

  @closed_pipe_safe
  def broken():
    raise BrokenPipeError
  assert ok() == 1 and broken() == 0


def test_quality_report_survives_every_corrupted_file(base, tmp_path):
  """krok-quality importuje przez #9, który każdy zły plik zamienia w problem z importu albo w czytelny błąd."""
  for name in ("groups.json", "rooms.json", "calendar.json"):
    for how in ("bad-json", "obj", "bad-utf8"):
      d = corrupted(base, tmp_path / f"{name}-{how}", name, how)
      assert quality_main(["--data", str(d), "-q"]) in (0, 2)
  assert json.loads((base / "_meta.json").read_text())["preset"] == "mini"


def test_dataset_from_partial_files_names_missing_file(base):
  data = read_files(base)
  del data["calendar.json"], data["rooms.json"]
  with pytest.raises(DatasetError, match="brak plików: calendar.json, rooms.json"):
    dataset_from_files(data)


def test_extends_cycle_fixture_is_valid_toml_for_windows_paths():
  """Regresja do recenzji: ścieżka z Windows w napisie TOML musi przejść przez parser bez zmian."""
  import tomllib
  win = r"C:\Users\michal\AppData\Local\Temp\b.toml"
  assert tomllib.loads(f"extends = {json.dumps(win, ensure_ascii=False)}\n")["extends"] == win


def test_generator_room_type_must_be_text(tmp_path):
  """Sala bez pojemności z typem innym niż tekst (np. lista) dawała TypeError przed walidacją modelu."""
  rooms = tmp_path / "rooms.json"
  rooms.write_text(json.dumps([{"id": "P1-001", "buildingId": "P1", "number": "001", "type": [], "facultyIds": ["WJ"]}]))
  assert gen_main(["--preset", "mini", "--rooms", str(rooms), "--out", str(tmp_path / "o"), "-q"]) == 2


def test_list_reports_invalid_preset_and_keeps_listing(monkeypatch, tmp_path, capsys):
  """Regresja z ostrzejszej walidacji presetów: --list wypisuje poprawne presety, błędny zgłasza i zwraca 2."""
  from krok.generator import settings
  presets = tmp_path / "presets"
  shutil.copytree(settings.PRESETS_DIR, presets)
  (presets / "zly.toml").write_text('name = "zly"\nfields = []\n')
  monkeypatch.setattr(settings, "PRESETS_DIR", presets)
  assert gen_main(["--list"]) == 2
  captured = capsys.readouterr()
  assert "mini" in captured.out and "wydzial" in captured.out
  assert "preset zly" in captured.err
