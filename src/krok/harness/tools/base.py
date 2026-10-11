from dataclasses import dataclass
from typing import Any, Callable
from pydantic import BaseModel

@dataclass(frozen=True)
class Tool:
  name: str
  description: str
  input: type[BaseModel]
  run: Callable[[Any], Any]