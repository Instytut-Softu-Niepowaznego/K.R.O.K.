import itertools
import json
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
  ChatCompletionMessageFunctionToolCall as ToolCall,
  Function,
)

import krok.harness.tools.save_plan as save_plan_module
from krok.harness import agent
from krok.harness.agent import MAX_STEPS, run_agent


def text(content):
  return ChatCompletionMessage(role="assistant", content=content)


def call(name, arguments):
  """Odpowiedź modelu z wywołaniem narzędzia; arguments to dict albo surowy tekst."""
  raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
  return ChatCompletionMessage(role="assistant", tool_calls=[
    ToolCall(id="call-1", type="function", function=Function(name=name, arguments=raw)),
  ])


class FakeClient:
  """Odtwarza zapisane odpowiedzi modelu i pamięć wysłanych wiadomości."""

  def __init__(self, replies):
    self.replies = iter(replies)
    self.calls = 0
    self.messages = []
    self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

  def create(self, model, messages, tools):
    self.calls += 1
    self.messages = messages
    return SimpleNamespace(choices=[SimpleNamespace(message=next(self.replies))])

  def tool_results(self):
    return [json.loads(m["content"]) for m in self.messages if m["role"] == "tool"]

  def nudges(self):
    return [m for m in self.messages if m["role"] == "user"][1:]


@pytest.fixture
def llm(monkeypatch):
  def install(*replies):
    client = FakeClient(replies)
    monkeypatch.setattr(agent, "client", client)
    return client
  return install


@pytest.fixture
def saved_plan(tmp_path, monkeypatch, valid_plan):
  """Argumenty poprawnego save_plan, z zapisem do katalogu tymczasowego."""
  monkeypatch.setattr(save_plan_module, "PLANS", tmp_path / "plans")
  return {"lessons": [l.model_dump() for l in valid_plan]}


def test_without_finish_tool_returns_first_text(llm):
  client = llm(text("gotowe"))

  assert run_agent("sys", "zadanie") == "gotowe"
  assert client.calls == 1


def test_finishes_after_finish_tool_succeeds(llm, saved_plan):
  client = llm(call("save_plan", saved_plan), text("zapisane"))

  assert run_agent("sys", "zadanie", finish_tool="save_plan") == "zapisane"
  assert client.nudges() == []


def test_nudges_when_model_stops_before_finish_tool(llm, saved_plan):
  client = llm(text("mam pytanie"), call("save_plan", saved_plan), text("zapisane"))

  assert run_agent("sys", "zadanie", finish_tool="save_plan") == "zapisane"
  assert len(client.nudges()) == 1
  assert "save_plan" in client.nudges()[0]["content"]


def test_failed_finish_tool_does_not_finish(llm, saved_plan):
  client = llm(call("save_plan", {"lessons": []}), text("koniec"), call("save_plan", saved_plan), text("zapisane"))

  assert run_agent("sys", "zadanie", finish_tool="save_plan") == "zapisane"
  assert "popraw je przed zapisem" in client.tool_results()[0]["error"]
  assert len(client.nudges()) == 1


def test_raises_when_nudges_run_out(llm):
  client = llm(*[text("nie umiem")] * 3)

  with pytest.raises(RuntimeError, match="save_plan.*nie umiem"):
    run_agent("sys", "zadanie", finish_tool="save_plan", max_nudges=2)
  assert client.calls == 3


def test_invalid_json_goes_back_to_model(llm):
  client = llm(call("validate_plan", "{zły json"), text("gotowe"))

  assert run_agent("sys", "zadanie") == "gotowe"
  assert "nie jest poprawny JSON" in client.tool_results()[0]["error"]


def test_unknown_tool_goes_back_to_model(llm):
  client = llm(call("brak", {}), text("gotowe"))

  assert run_agent("sys", "zadanie") == "gotowe"
  assert client.tool_results() == [{"error": "Nieznane narzędzie: brak"}]


def test_raises_after_max_steps(llm):
  client = llm(*itertools.repeat(call("validate_plan", {"lessons": []}), MAX_STEPS + 1))

  with pytest.raises(RuntimeError, match=f"{MAX_STEPS} krokach"):
    run_agent("sys", "zadanie")
  assert client.calls == MAX_STEPS
