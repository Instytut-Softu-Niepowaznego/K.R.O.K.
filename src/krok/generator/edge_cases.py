"""Przypadki brzegowe (docs/etap1/model-domeny.md, „Generator (#7)”).

Każdy przypadek dopisuje do `cases` wpis {case, description, ids}, który trafia do _meta.json —
percepcja (#10, #11) i solver mogą sprawdzić, czy wykryły dokładnie te problemy.
"""

import datetime as dt
from dataclasses import dataclass, field

from krok.model import Availability, Calendar, Class, DayOverride, Group, Holiday, Lecturer, Rector, ResourceBlock, Room, Scope, Subject

from .lecturers import class_modes


@dataclass
class World:
  faculty_id: str
  groups: list[Group]
  programs: list
  rooms: list[Room]
  lecturers: list[Lecturer]
  subjects: list[Subject]
  classes: list[Class]
  calendar: Calendar
  cases: list[dict] = field(default_factory=list)

  def note(self, case: str, description: str, ids: list[str]) -> None:
    self.cases.append({"case": case, "description": description, "ids": ids})

  def lecturer(self, comp: str, availability: list[Availability], limit: int) -> Lecturer:
    lec = Lecturer(id=f"P-{len(self.lecturers) + 1:04d}", name=f"dr Przypadek Brzegowy {len(self.lecturers) + 1}",
                   faculty_ids=[self.faculty_id], competences=[comp], home_building_id=self.rooms[0].building_id,
                   availability=availability, max_units_weekly=limit)
    self.lecturers.append(lec)
    return lec

  def comp(self, c: Class) -> str:
    return next(s.competence for s in self.subjects if s.id == c.subject_id)

  def add_subject(self, sid: str, name: str, comp: str) -> Subject:
    s = Subject(id=sid, name=name, competence=comp)
    self.subjects.append(s)
    return s


def oversized_group(w: World) -> None:
  by_id = {g.id: g for g in w.groups}
  c = next(c for c in w.classes if c.form == "L")
  biggest = max(r.capacity for r in w.rooms if r.type == c.room_type)
  g = by_id[c.group_id]
  g.size = biggest + 4
  parent = by_id.get(g.parent_id)
  while parent:  # rodzic nie może być mniejszy od dziecka
    parent.size = max(parent.size, sum(x.size for x in w.groups if x.parent_id == parent.id))
    parent = by_id.get(parent.parent_id)
  ids = [x.id for x in w.classes if x.group_id == g.id and x.room_type == c.room_type]
  w.note("grupa-wieksza-od-sal", f"Grupa {g.id} ({g.size} os.) większa od każdej sali typu {c.room_type} (max {biggest})",
         [g.id, *ids])


def subject_without_lecturer(w: World) -> None:
  group = next(g for g in w.groups if g.kind == "lecture")
  s = w.add_subject("EDGE-BEZ-PROW", "Przedmiot bez prowadzącego", "kompetencja-bez-prowadzacego")
  c = Class(id=f"{s.id}-W-{group.id}", subject_id=s.id, form="W", group_id=group.id, units_per_week=2,
            units_per_meeting=2, parity="every", room_type="lecture-hall")
  w.classes.append(c)
  w.note("przedmiot-bez-prowadzacego", "Żaden prowadzący nie ma kompetencji kompetencja-bez-prowadzacego", [s.id, c.id])


def lecturer_without_availability(w: World) -> None:
  c = next(c for c in w.classes if c.lecturer_id is None and c.form == "C")
  lec = w.lecturer(w.comp(c), [], 10)
  c.lecturer_id = lec.id
  w.note("prowadzacy-bez-dostepnosci", f"{lec.id} nie ma żadnej dostępności, a ma przydzielone zajęcia {c.id}", [lec.id, c.id])


def lecturer_over_limit(w: World) -> None:
  first = next(c for c in w.classes if c.lecturer_id is None and c.form == "L")
  same = [c for c in w.classes if c.lecturer_id is None and c.subject_id == first.subject_id and c.form == "L"][:3]
  lec = w.lecturer(w.comp(first), [Availability(day=d, from_="08:00", to="18:00") for d in ("Pn", "Wt", "Śr")], 2)
  for c in same:
    c.lecturer_id = lec.id
  load = sum(c.units_per_week for c in same)
  w.note("limit-ponizej-obciazenia", f"{lec.id} ma limit 2 jednostki, a przydzielone {load}", [lec.id, *(c.id for c in same)])


def one_unit_and_ab(w: World) -> None:
  ex = next(g for g in w.groups if g.kind == "exercise")
  labs = [g for g in w.groups if g.parent_id == ex.id and g.kind == "lab"][:2]
  comp = w.comp(next(c for c in w.classes if c.form == "L"))
  s1 = w.add_subject("EDGE-1J", "Konsultacje projektowe (1 jednostka)", comp)
  one = Class(id=f"{s1.id}-C-{ex.id}", subject_id=s1.id, form="C", group_id=ex.id, units_per_week=1,
              units_per_meeting=1, parity="every", room_type="classroom")
  s2 = w.add_subject("EDGE-AB", "Laboratorium co dwa tygodnie (A/B)", comp)
  ab = [Class(id=f"{s2.id}-L-{g.id}", subject_id=s2.id, form="L", group_id=g.id, units_per_week=2, units_per_meeting=2,
              parity=p, room_type="lab-it") for g, p in zip(labs, "AB")]
  w.classes += [one, *ab]
  w.note("zajecia-1-jednostkowe", "Zajęcia trwające jedną jednostkę (45 min)", [one.id])
  w.note("zajecia-ab", "Laboratoria grup rodzeństwa w tygodniach A i B", [c.id for c in ab])


def lecturer_both_modes(w: World) -> None:
  modes = class_modes(w.classes, w.groups, w.programs)
  free = [c for c in w.classes if c.lecturer_id is None]
  ft_by_comp = {}
  for c in free:
    if modes[c.id] == "full-time":
      ft_by_comp.setdefault(w.comp(c), c)
  pair = next(((ft_by_comp[w.comp(c)], c) for c in free if modes[c.id] == "part-time" and w.comp(c) in ft_by_comp), None)
  if pair is None:
    return
  ft, pt = pair
  lec = w.lecturer(w.comp(pt),[Availability(day="Wt", from_="08:00", to="16:00"), Availability(day="So", from_="08:00", to="18:00")], 20)
  pt.lecturer_id = ft.lecturer_id = lec.id
  w.note("prowadzacy-dzienne-i-zaoczne", f"{lec.id} uczy na studiach dziennych i zaocznych", [lec.id, ft.id, pt.id])


def class_longer_than_availability(w: World) -> None:
  c = next(c for c in w.classes if c.lecturer_id is None and c.units_per_meeting >= 2)
  lec = w.lecturer(w.comp(c), [Availability(day="Cz", from_="08:00", to="09:00")], 10)
  c.lecturer_id = lec.id
  w.note("zajecia-dluzsze-niz-dostepnosc", f"{c.id} trwa {c.units_per_meeting} jednostki, a {lec.id} ma tylko 60 min ciągłej dostępności",
         [lec.id, c.id])


def overlapping_exceptions(w: World) -> None:
  cal = w.calendar
  holidays = [e for e in cal.exceptions if isinstance(e, Holiday)]
  weekday_holiday = next(e for e in holidays if e.from_ == e.to and e.from_.weekday() < 5)
  longest = max(holidays, key=lambda e: (e.to - e.from_, e.id))
  inside = longest.from_ + dt.timedelta(days=1)
  rector = Rector(id="edge-rektorskie-w-przerwie", type="rector", from_=inside, to=inside, label="Dzień rektorski w trakcie przerwy")
  block = ResourceBlock(id="edge-blokada-przez-swieto", type="resourceBlock", from_=weekday_holiday.from_ - dt.timedelta(days=1),
                        to=weekday_holiday.from_ + dt.timedelta(days=1), scope=Scope(room_ids=[w.rooms[0].id]),
                        label="Blokada sali obejmująca święto")
  makeup = DayOverride(id="edge-odpracowanie-w-swieto", type="dayOverride", date=weekday_holiday.from_, follows_day="Pt",
                       follows_parity="A", label="Odpracowanie wypadające w dniu wolnym")
  cal.exceptions = [*cal.exceptions, rector, block, makeup]
  w.note("nakladajace-sie-wyjatki", f"Wyjątki nakładające się na {longest.id} i {weekday_holiday.id}", [rector.id, block.id, longest.id])
  w.note("odpracowanie-w-dniu-wolnym", f"dayOverride w dniu {weekday_holiday.from_} ({weekday_holiday.label})",
         [makeup.id, weekday_holiday.id])


CASES = [oversized_group, subject_without_lecturer, lecturer_without_availability, lecturer_over_limit, one_unit_and_ab,
         lecturer_both_modes, class_longer_than_availability, overlapping_exceptions]


def inject(w: World) -> list[dict]:
  for case in CASES:
    case(w)
  return w.cases
