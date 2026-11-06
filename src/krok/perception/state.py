"""Stan otoczenia: dane wejściowe przetworzone na to, czego potrzebuje planowanie (#10)."""

from collections import defaultdict
from dataclasses import dataclass, field

from krok.model import Class, Dataset, Group, ResourceBlock, Room, to_hhmm, to_minutes

from .measures import Interval, clip, day_window, fits, meeting_minutes, merge, subtract, total, travel_matrix

WEEKS = ("A", "B")


@dataclass
class ClassInfo:
  """Zajęcia z policzonymi wielkościami: tryb, czas spotkania, kto może je prowadzić."""

  cls: Class
  group: Group
  study_mode: str
  competence: str
  meeting_minutes: int  # najkrótszy możliwy czas jednego spotkania
  eligible: list[str]  # prowadzący z prawem do zajęć: przydział na sztywno, pula albo kompetencja
  available: list[str] = field(default_factory=list)  # z nich ci, którym zajęcia mieszczą się w dostępności

  @property
  def meetings(self) -> int:
    return self.cls.meetings_per_week

  def in_week(self, week: str) -> bool:
    return self.cls.parity in ("every", week)

  @property
  def load(self) -> float:
    """Średnie tygodniowe obciążenie w jednostkach (A/B co drugi tydzień)."""
    return self.cls.units_per_week * (1 if self.cls.parity == "every" else 0.5)


@dataclass
class EnvironmentState:
  """Stan otoczenia: macierze czasu i dostępności, sale, zajęcia i plany studentów."""

  dataset: Dataset
  travel: dict[str, dict[str, float]]
  lecturer_availability: dict[str, dict[str, list[Interval]]]  # prowadzący → dzień → przedziały
  room_availability: dict[str, dict[str, list[Interval]]]  # sala → dzień → przedziały (po blokadach)
  group_availability: dict[str, dict[str, list[Interval]]]  # grupa → dzień → przedziały (po blokadach)
  rooms_by_type: dict[str, list[Room]]  # typ → sale dostępne w semestrze, od największej
  classes: dict[str, ClassInfo]
  leaf_groups: list[Group]
  ancestors: dict[str, list[str]]  # grupa → ona sama i jej przodkowie
  full_semester_blocks: dict[str, list[str]]  # "rooms" / "lecturers" / "groups" → id

  def mode_days(self, mode: str) -> list[str]:
    return self.dataset.config.time_grid.days_by_study_mode[mode]

  def available_minutes(self, lecturer_id: str, mode: str) -> int:
    days = self.lecturer_availability.get(lecturer_id, {})
    return sum(total(days.get(d, [])) for d in self.mode_days(mode))

  def group_available_minutes(self, group_id: str, mode: str) -> int:
    """Czas grupy w dniach trybu studiów po blokadach na cały semestr (także częściowych, z godzinami)."""
    days = self.group_availability.get(group_id, {})
    return sum(total(days.get(d, [])) for d in self.mode_days(mode))

  def longest_window(self, lecturer_id: str, mode: str) -> int:
    days = self.lecturer_availability.get(lecturer_id, {})
    return max((e - s for d in self.mode_days(mode) for s, e in days.get(d, [])), default=0)

  def can_teach(self, lecturer_id: str, info: ClassInfo) -> bool:
    """Czy choć jedno spotkanie zajęć mieści się w którymś oknie dostępności w dniach trybu studiów."""
    grid = self.dataset.config.time_grid
    days = self.lecturer_availability.get(lecturer_id, {})
    return any(fits(w, info.meeting_minutes, grid) for d in self.mode_days(info.study_mode) for w in days.get(d, []))

  def rooms_by_capacity(self) -> dict[str, dict[str, int]]:
    """Sale pogrupowane po typie i pojemności: typ → pojemność → liczba sal (tylko sale dostępne w semestrze)."""
    out: dict[str, dict[str, int]] = {}
    for room_type, rooms in sorted(self.rooms_by_type.items()):
      counts: dict[str, int] = {}
      for r in rooms:
        counts[str(r.capacity)] = counts.get(str(r.capacity), 0) + 1
      out[room_type] = counts
    return out

  def export(self) -> dict:
    """Stan otoczenia jako JSON: macierz przejść, macierz dostępności prowadzących, sale wg typu i pojemności."""
    def hhmm(intervals):
      return [[to_hhmm(s), to_hhmm(e)] for s, e in intervals]
    return {
      "travelMinutes": self.travel,
      "lecturerAvailability": {lid: {d: hhmm(iv) for d, iv in days.items()} for lid, days in self.lecturer_availability.items()},
      "roomsByType": {t: [r.id for r in rooms] for t, rooms in sorted(self.rooms_by_type.items())},
      "roomsByCapacity": self.rooms_by_capacity(),
      "fullSemesterBlocks": self.full_semester_blocks,
      "classes": {cid: {"studyMode": i.study_mode, "meetingMinutes": i.meeting_minutes, "eligible": i.eligible,
                        "available": i.available} for cid, i in self.classes.items()},
    }

  def student_classes(self, leaf_id: str, week: str) -> list[ClassInfo]:
    """Plan studenta grupy-liścia w tygodniu A lub B: zajęcia grupy i wszystkich jej przodków."""
    groups = set(self.ancestors[leaf_id])
    return [c for c in self.classes.values() if c.cls.group_id in groups and c.in_week(week)]


def _is_full_semester(block: ResourceBlock, ds: Dataset) -> bool:
  return block.from_ <= ds.calendar.semester_start and block.to >= ds.calendar.semester_end


def build_state(ds: Dataset) -> EnvironmentState:
  grid = ds.config.time_grid
  window = day_window(grid)
  groups = {g.id: g for g in ds.groups}
  mode = {p.id: p.study_mode for p in ds.programs}
  subjects = {s.id: s for s in ds.subjects}

  children = defaultdict(list)
  for g in ds.groups:
    if g.parent_id:
      children[g.parent_id].append(g.id)
  ancestors = {}
  for g in ds.groups:
    chain, cur = [], g
    while cur:
      chain.append(cur.id)
      cur = groups.get(cur.parent_id) if cur.parent_id else None
    ancestors[g.id] = chain

  def descendants(gid: str) -> list[str]:
    out, stack = [], [gid]
    while stack:
      cur = stack.pop()
      out.append(cur)
      stack += children[cur]
    return out

  field_faculty = {f.id: f.faculty_id for f in ds.fields}
  group_faculty = {g.id: field_faculty.get(next((p.field_id for p in ds.programs if p.id == g.program_id), ""))
                   for g in ds.groups}

  def targets(scope) -> dict[str, list[str]]:
    """Zasoby objęte blokadą. Podane id (grupy z potomkami) zawężają do siebie, `facultyIds` do zasobów wydziału,
    a pusty `scope` obejmuje wszystko (model domeny: „brak = wszystkie dane”)."""
    faculties = set(scope.faculty_ids or [])
    def in_faculty(ids_faculties):
      return not faculties or bool(faculties & set(ids_faculties))
    specific = any(x is not None for x in (scope.room_ids, scope.lecturer_ids, scope.group_ids))
    if specific:
      rooms = scope.room_ids or []
      lecturers = scope.lecturer_ids or []
      group_ids = [d for g in scope.group_ids or [] for d in descendants(g)]
    else:
      rooms, lecturers, group_ids = [r.id for r in ds.rooms], [lec.id for lec in ds.lecturers], [g.id for g in ds.groups]
    room_fac = {r.id: r.faculty_ids for r in ds.rooms}
    lec_fac = {lec.id: lec.faculty_ids for lec in ds.lecturers}
    return {
      "rooms": [i for i in rooms if in_faculty(room_fac.get(i, []))],
      "lecturers": [i for i in lecturers if in_faculty(lec_fac.get(i, []))],
      "groups": [i for i in group_ids if in_faculty([group_faculty.get(i)] if group_faculty.get(i) else [])],
    }

  # Blokady na cały semestr działają jak brak dostępności (model domeny, „Kalendarz semestru i wyjątki”).
  # Przy wyłączonej regule blokady-zasobow plan może je łamać, więc wtedy ich nie stosujemy.
  blocks = {"rooms": [], "lecturers": [], "groups": []}
  # (rodzaj zasobu, id) → wycięte przedziały (każdego dnia). Id są unikalne tylko w obrębie kolekcji,
  # więc blokada grupy nie może trafić do prowadzącego albo sali o tym samym id.
  cuts: dict[tuple[str, str], list[Interval]] = defaultdict(list)
  apply_blocks = any(r.id == "blokady-zasobow" and r.enabled for r in ds.config.rules)
  for e in ds.calendar.exceptions if apply_blocks else []:
    if not isinstance(e, ResourceBlock) or not _is_full_semester(e, ds):
      continue
    cut = (to_minutes(e.from_time), to_minutes(e.to_time)) if e.from_time else window
    for key, ids in targets(e.scope).items():
      for i in ids:
        cuts[(key, i)].append(cut)
        if cut[0] <= window[0] and cut[1] >= window[1]:  # obejmuje cały dzień zajęć
          blocks[key].append(i)

  def available(base: dict[str, list[Interval]], key: str, rid: str) -> dict[str, list[Interval]]:
    out = {}
    for day, intervals in base.items():
      ivs = merge(clip(intervals, window))
      for cut in cuts.get((key, rid), []):
        ivs = subtract(ivs, cut)
      if ivs:
        out[day] = ivs
    return out

  lecturer_av = {}
  for lec in ds.lecturers:
    raw = defaultdict(list)
    for a in lec.availability:
      raw[a.day].append((to_minutes(a.from_), to_minutes(a.to)))
    lecturer_av[lec.id] = available(raw, "lecturers", lec.id)
  room_av = {r.id: available({d: [window] for d in grid.days}, "rooms", r.id) for r in ds.rooms}
  # Czas grupy po blokadach; blokada przodka obejmuje potomków, bo targets() rozwija grupy w dół.
  group_av = {g.id: available({d: [window] for d in grid.days}, "groups", g.id) for g in ds.groups}

  rooms_by_type = defaultdict(list)
  for r in sorted(ds.rooms, key=lambda r: (-r.capacity, r.id)):
    if room_av[r.id]:
      rooms_by_type[r.type].append(r)

  by_competence = defaultdict(list)
  for lec in ds.lecturers:
    for c in lec.competences:
      by_competence[c].append(lec.id)

  classes = {}
  for c in ds.classes:
    subject = subjects[c.subject_id]
    if c.lecturer_id:
      eligible = [c.lecturer_id]
    elif subject.pool(c.form) is not None:
      eligible = list(subject.pool(c.form))
    else:
      eligible = list(by_competence.get(subject.competence, []))
    info = ClassInfo(c, groups[c.group_id], mode[groups[c.group_id].program_id], subject.competence,
                     meeting_minutes(c.units_per_meeting, grid), eligible)
    classes[c.id] = info

  state = EnvironmentState(
    dataset=ds, travel=travel_matrix(ds.buildings, ds.config), lecturer_availability=lecturer_av, room_availability=room_av,
    group_availability=group_av,
    rooms_by_type=dict(rooms_by_type), classes=classes, leaf_groups=[g for g in ds.groups if not children[g.id]],
    ancestors=ancestors, full_semester_blocks=blocks,
  )
  for info in classes.values():
    info.available = [lid for lid in info.eligible if state.can_teach(lid, info)]
  return state
