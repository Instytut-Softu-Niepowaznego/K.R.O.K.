from pydantic import ValidationError
from .tools.get_data import get_data
from .tools.validate_plan import validate_plan
from .tools.save_plan import save_plan

TOOLS = {t.name: t for t in [get_data, validate_plan, save_plan]}

def definitions():
  return [{"type": "function", "function": {
    "name": t.name, "description": t.description,
    "parameters": t.input.model_json_schema()
  }} for t in TOOLS.values()]

def run_tool(name: str, raw_args: dict):
  tool = TOOLS.get(name)
  if not tool:
    return {"error": f"Nieznane narzędzie: {name}"}
  try:
    return {"result": tool.run(tool.input.model_validate(raw_args))}
  except ValidationError as e:
    return {"error": str(e)}
  except Exception as e:
    return {"error": repr(e)}