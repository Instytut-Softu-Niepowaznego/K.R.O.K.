"""Odporność CLI: zepsute dane kończą się komunikatem i kodem 2, a nie tracebackiem."""

import shutil
from pathlib import Path

import pytest

from krok.db.cli import main as db_main
from krok.events.cli import main as events_main
from krok.perception.cli import main as env_main
from krok.perception.quality_cli import main as quality_main

CORRUPT = {"directory": None, "bad-utf8": b"\xff\xfe", "bad-json": b"{zly", "obj": b"{}", "null": b"null"}


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
  out = tmp_path_factory.mktemp("dane")
  assert events_main(["generate", "--preset", "mini", "--out", str(out), "-q"]) == 0
  return out


@pytest.mark.parametrize("how", CORRUPT)
def test_every_data_cli_rejects_corrupted_file_with_exit_2(base, tmp_path, capsys, how):
  d = tmp_path / "dane"
  shutil.copytree(base, d)
  (d / "calendar.json").unlink()
  if CORRUPT[how] is None:
    (d / "calendar.json").mkdir()
  else:
    (d / "calendar.json").write_bytes(CORRUPT[how])

  assert env_main(["--data", str(d), "-q"]) == 2
  assert events_main(["generate", "--data", str(d), "-q"]) == 2
  assert db_main(["--db", str(tmp_path / "krok.db"), "import", str(d)]) == 2
  assert quality_main(["--data", str(d), "-q"]) == 2
  assert "calendar.json" in capsys.readouterr().err
