"""Kalendarz semestru: święta, przerwy, dni i godziny rektorskie, blokady sal, odpracowania, zjazdy zaoczne."""

import datetime as dt
import random

from krok.model import Calendar, DayOverride, Holiday, PartTimeSessions, Rector, ResourceBlock, Room, Scope, WeekParityOverride

from .settings import Preset

WEEKDAYS = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]


def easter(year: int) -> dt.date:
  """Wielkanoc (algorytm Meeusa/Jonesa/Butchera)."""
  a, b, c = year % 19, year // 100, year % 100
  d, e = b // 4, b % 4
  f = (b + 8) // 25
  g = (b - f + 1) // 3
  h = (19 * a + b - d - g + 15) % 30
  i, k = c // 4, c % 4
  ll = (32 + 2 * e + 2 * i - h - k) % 7
  m = (a + 11 * h + 22 * ll) // 451
  month, day = divmod(h + ll - 7 * m + 114, 31)
  return dt.date(year, month, day + 1)


def public_holidays(year: int) -> list[tuple[dt.date, str]]:
  e = easter(year)
  fixed = [((1, 1), "Nowy Rok"), ((1, 6), "Trzech Króli"), ((5, 1), "Święto Pracy"), ((5, 3), "Święto Konstytucji 3 Maja"),
           ((8, 15), "Wniebowzięcie NMP"), ((11, 1), "Wszystkich Świętych"), ((11, 11), "Święto Niepodległości"),
           ((12, 24), "Wigilia"), ((12, 25), "Boże Narodzenie"), ((12, 26), "Drugi dzień Bożego Narodzenia")]
  movable = [(e + dt.timedelta(days=1), "Poniedziałek Wielkanocny"), (e + dt.timedelta(days=60), "Boże Ciało")]
  return sorted([(dt.date(year, m, d), name) for (m, d), name in fixed] + movable)


def slug(text: str) -> str:
  table = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ ", "acelnoszzACELNOSZZ-")
  return "".join(ch for ch in text.translate(table).lower() if ch.isalnum() or ch == "-")


def _days(start: dt.date, end: dt.date):
  d = start
  while d <= end:
    yield d
    d += dt.timedelta(days=1)


def build_calendar(preset: Preset, rooms: list[Room], rng: random.Random) -> Calendar:
  spec = preset.calendar
  start, end = spec.semester_start, spec.semester_end
  exceptions = []
  off: set[dt.date] = set()

  for year in range(start.year, end.year + 1):
    for day, name in public_holidays(year):
      if start <= day <= end:
        exceptions.append(Holiday(id=f"{slug(name)}-{year}", type="holiday", from_=day, to=day, label=name))
        off.add(day)
  for b_from, b_to, label in spec.breaks:
    exceptions.append(Holiday(id=f"{slug(label)}-{b_from.year}", type="holiday", from_=b_from, to=b_to, label=label))
    off.update(_days(b_from, b_to))

  teaching = [d for d in _days(start, end) if d.weekday() < 5 and d not in off]
  picked = sorted(rng.sample(teaching, min(len(teaching), spec.rector_days + spec.rector_hours + 1)))
  rng.shuffle(picked)
  for i in range(spec.rector_days):
    d = picked.pop()
    exceptions.append(Rector(id=f"rektorskie-{i + 1}", type="rector", from_=d, to=d, label="Dzień rektorski"))
  for i in range(spec.rector_hours):
    d = picked.pop()
    exceptions.append(Rector(id=f"rektorskie-godz-{i + 1}", type="rector", from_=d, to=d, from_time="12:00", to_time="20:00",
                             scope=Scope(faculty_ids=[preset.faculty_id]), label="Godziny dziekańskie"))
  if spec.day_override and picked:
    d = picked.pop()
    follows = WEEKDAYS[(d.weekday() + rng.randint(1, 4)) % 5]
    exceptions.append(DayOverride(id="odpracowanie-1", type="dayOverride", date=d, follows_day=follows,
                                  follows_parity=rng.choice(["A", "B"]),
                                  label=f"{WEEKDAYS[d.weekday()]} z zajęciami jak w {follows}"))
  for i, room in enumerate(rng.sample(rooms, min(len(rooms), spec.room_blocks))):
    b_from = rng.choice(teaching)
    b_to = b_from + dt.timedelta(days=rng.randint(4, 13))
    exceptions.append(ResourceBlock(id=f"blokada-sali-{i + 1}", type="resourceBlock", from_=b_from, to=min(b_to, end),
                                    scope=Scope(room_ids=[room.id]), label=f"Remont sali {room.id}"))

  monday = start - dt.timedelta(days=start.weekday())
  overrides = []
  while monday <= end:
    week = [monday + dt.timedelta(days=i) for i in range(5)]
    if all(d in off or not start <= d <= end for d in week) and any(start <= d <= end for d in week):
      overrides.append(WeekParityOverride(week_of=monday, parity=None))
    monday += dt.timedelta(days=7)

  first_weekend = start + dt.timedelta(days=(5 - start.weekday()) % 7)
  return Calendar(semester_start=start, semester_end=end, first_week_parity=spec.first_week_parity,
                  part_time_sessions=PartTimeSessions(first_weekend=first_weekend, every_weeks=spec.part_time_every_weeks),
                  week_parity_overrides=overrides, exceptions=exceptions)
