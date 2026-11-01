from krok.harness.registry import TOOLS, definitions, run_tool


def test_definitions_describe_every_tool():
  defs = definitions()

  assert {d["function"]["name"] for d in defs} == set(TOOLS)
  for d in defs:
    assert d["type"] == "function"
    assert d["function"]["description"]
    assert d["function"]["parameters"]["type"] == "object"


def test_runs_tool_and_wraps_result(valid_plan):
  args = {"lessons": [l.model_dump() for l in valid_plan]}

  assert run_tool("validate_plan", args) == {"result": {"ok": True, "errors": [], "warnings": []}}


def test_failed_validation_is_a_result_not_an_error():
  result = run_tool("validate_plan", {"lessons": []})

  assert result["result"]["ok"] is False
  assert result["result"]["errors"]


def test_unknown_tool_returns_error():
  assert run_tool("brak", {}) == {"error": "Nieznane narzędzie: brak"}


def test_bad_arguments_return_error():
  assert "error" in run_tool("validate_plan", {"lessons": "to nie lista"})


def test_exception_in_tool_returns_error():
  # save_plan rzuca wyjątek dla planu z błędami.
  result = run_tool("save_plan", {"lessons": []})

  assert "popraw je przed zapisem" in result["error"]
