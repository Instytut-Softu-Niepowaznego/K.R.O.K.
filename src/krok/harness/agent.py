import json
import time
from .llm import client, MODEL
from .registry import definitions, run_tool

MAX_STEPS = 20

def run_agent(system: str, user_prompt: str, finish_tool: str | None = None, max_nudges: int = 3) -> str:
  """Pętla agenta. Jeśli podano finish_tool, agent nie może skończyć, dopóki to narzędzie nie zadziała bez błędu."""
  messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": user_prompt}
  ]

  tools = definitions()
  finished = finish_tool is None
  nudges = 0

  for step in range(MAX_STEPS):
    print(f"[{step}] model myśli...", flush=True)
    t = time.time()
    response = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
    print(f"[{step}] odpowiedź po {time.time() - t:.0f}s", flush=True)
    msg = response.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))

    if not msg.tool_calls:
      if finished:
        return msg.content or ""
      if nudges >= max_nudges:
        raise RuntimeError(f"Agent skończył bez {finish_tool} mimo {max_nudges} ponagleń. Ostatnia odpowiedź: {msg.content}")
      nudges += 1
      print(f"[{step}] model skończył bez {finish_tool}, ponaglam ({nudges}/{max_nudges})", flush=True)
      messages.append({"role": "user", "content":
        f"Zadanie nie jest skończone: {finish_tool} nie zadziałało jeszcze bez błędu. "
        "Nie zadawaj pytań. Popraw wszystkie błędy z walidacji i kontynuuj."})
      continue

    for call in msg.tool_calls:
      name = call.function.name

      try: 
        args = json.loads(call.function.arguments or "{}")
        result = run_tool(name, args)
      except json.JSONDecodeError as e:
        result = {"error": f"Argumenty to nie jest poprawny JSON: {e}"}

      if name == finish_tool and "error" not in result:
        finished = True

      print(f"[{step}] {name}({call.function.arguments[:80]}...) -> {str(result)[:300]}", flush=True)
      messages.append({
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(result, ensure_ascii=False), 
      })

  raise RuntimeError(f"Agent nie skończył w {MAX_STEPS} krokach")