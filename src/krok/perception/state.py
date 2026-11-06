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

  # Blokady na cały semestr działają jak brak dostępności (model domeny, „Kalendarz semestru i wyjątki”).
  blocks = {"rooms": [], "lecturers": [], "groups": []}
  cuts: dict[str, list[Interval]] = defaultdict(list)  # id → wycięte przedziały (każdego dnia)
  for e in ds.calendar.exceptions:
    if not isinstance(e, ResourceBlock) or not _is_full_semester(e, ds):
      continue
    cut = (to_minutes(e.from_time), to_minutes(e.to_time)) if e.from_time else window
    for key, ids in (("rooms", e.scope.room_ids), ("lecturers", e.scope.lecturer_ids),
                     ("groups", [d for g in e.scope.group_ids or [] for d in descendants(g)])):
      for i in ids or []:
        cuts[i].append(cut)
        if cut[0] <= window[0] and cut[1] >= window[1]:  # obejmuje cały dzień zajęć
          blocks[key].append(i)

  def available(base: dict[str, list[Interval]], rid: str) -> dict[str, list[Interval]]:
    out = {}
    for day, intervals in base.items():
      ivs = merge(clip(intervals, window))
      for cut in cuts.get(rid, []):
        ivs = subtract(ivs, cut)
      if ivs:
        out[day] = ivs
    return out

  lecturer_av = {}
  for lec in ds.lecturers:
    raw = defaultdict(list)
    for a in lec.availability:
      raw[a.day].append((to_minutes(a.from_), to_minutes(a.to)))
    lecturer_av[lec.id] = available(raw, lec.id)
  room_av = {r.id: available({d: [window] for d in grid.days}, r.id) for r in ds.rooms}

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
    rooms_by_type=dict(rooms_by_type), classes=classes, leaf_groups=[g for g in ds.groups if not children[g.id]],
    ancestors=ancestors, full_semester_blocks=blocks,
  )
  for info in classes.values():
    info.available = [lid for lid in info.eligible if state.can_teach(lid, info)]
  return state
