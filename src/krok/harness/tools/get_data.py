from typing import Literal
from pydantic import BaseModel
from krok.db import connect, read_dataset
from krok.db.legacy import legacy_view
from .base import Tool

class GetDataInput(BaseModel):
  type: Literal["buildings", "student-groups", "workers"]

def load(name: str):
  """Czyta dane z bazy w kształcie starego prototypu."""
  engine = connect()
  try:
    return legacy_view(read_dataset(engine), name)
  finally:
    engine.dispose()

def _run(args: GetDataInput):
  """Wykonuje narzędzie `get_data`."""
  return load(args.type)

get_data = Tool("get_data", "Zwraca z bazy budynki z salami, grupy z kursami lub prowadzących.", GetDataInput, _run)
