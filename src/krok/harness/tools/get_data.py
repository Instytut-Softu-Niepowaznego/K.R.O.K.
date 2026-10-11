import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel
from .base import Tool

DATA = Path(__file__).parents[2] / "data"

class GetDataInput(BaseModel):
  type: Literal["buildings", "student-groups", "workers"]

def load(name: str):
  return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))

def _run(args: GetDataInput):
  return load(args.type)

get_data = Tool("get_data", "Zwraca budynki z salami, grupy z kursami lub prowadzących.", GetDataInput, _run)