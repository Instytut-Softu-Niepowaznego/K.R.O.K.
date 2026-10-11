from collections import Counter, defaultdict
from typing import Literal
from pydantic import BaseModel, Field
from .tools.get_data import load

DAYS = ["Pon", "Wt", "Śr", "Czw", "Pt"]

class Lesson(BaseModel):
  day: Literal["Pon", "Wt", "Śr", "Czw", "Pt"]
  hour: int = Field(ge=8, le=17, description="Godzina rozpoczęcia; każda lekcja trwa 1h")
  group: str
  course: str
  building: str
  room: str
  worker: str

class PlanInput(BaseModel):
  lessons: list[Lesson] = Field(description="Cały plan tygodnia. Kurs 2h/tydz. = 2 lekcje.")

def validate(lessons: list[Lesson]) -> tuple[list[str], list[str]]:
  rooms = {(b["name"], r["number"]): r["profession"] for b in load("buildings") for r in b["rooms"]}
  courses = {(g["name"], c["name"]): c for g in load("student-groups") for c in g["courses"]}
  workers = {w["name"]: w for w in load("workers")}

  errors, warnings = [], []
  taken: dict[tuple, int] = {}
  hours = Counter()

  for i, l in enumerate(lessons):
    where = f"#{i} {l.day} {l.hour}:00 {l.group} / {l.course}"
    course = courses.get((l.group, l.course))
    room_type = rooms.get((l.building, l.room))
    worker = workers.get(l.worker)

    if not course:
      errors.append(f"{where}: grupa nie ma takiego kursu")
    if room_type is None:
      errors.append(f"{where}: nie ma sali {l.room} w budynku {l.building}")
    if not worker:
      errors.append(f"{where}: nie ma prowadzącego {l.worker}")

    if course and room_type and course["profession"] != room_type:
      errors.append(f"{where}: sala {l.building}/{l.room} jest typu {room_type}, kurs wymaga {course['profession']}")
    if course and worker and course["domain"] not in worker["Profesion"]:
      errors.append(f"{where}: {l.worker} nie uczy {course['domain']}")

    for key, label in [(("sala", l.day, l.hour, l.building, l.room), "sala zajęta"),
                       (("prow", l.day, l.hour, l.worker), f"{l.worker} ma już zajęcia"),
                       (("grupa", l.day, l.hour, l.group), "grupa ma już zajęcia")]:
      if key in taken:
        errors.append(f"{where}: {label} (koliduje z #{taken[key]})")
      else:
        taken[key] = i

    if course:
      hours[(l.group, l.course)] += 1
    if worker and l.building != worker["FavouriteBuilding"]:
      warnings.append(f"{where}: {l.worker} poza ulubionym budynkiem ({worker['FavouriteBuilding']})")

  for (group, name), c in courses.items():
    if hours[(group, name)] != c["hoursWeekly"]:
      errors.append(f"{group} / {name}: {hours[(group, name)]}h w planie, wymagane {c['hoursWeekly']}h")

  by_worker_day = defaultdict(list)
  for l in lessons:
    by_worker_day[(l.worker, l.day)].append(l)
  for (w, day), ls in by_worker_day.items():
    ls.sort(key=lambda l: l.hour)
    switches = sum(a.building != b.building for a, b in zip(ls, ls[1:]))
    if switches:
      warnings.append(f"{w} w {day}: {switches} zmian(y) budynku")

  return errors, warnings
