"""Miary bazowe: czas przejścia między budynkami, czas trwania zajęć, okna czasowe.

Wzory są wspólne dla percepcji i walidatora (docs/etap1/kryteria.md, „Pojęcia używane w pomiarach”).
"""

import math

from krok.model import Building, Config, TimeGrid, to_minutes

EARTH_RADIUS_KM = 6371.0088

Interval = tuple[int, int]  # minuty od północy, [start, koniec)


def distance_km(a: Building, b: Building) -> float:
  """Odległość w linii prostej po powierzchni Ziemi (wzór haversine)."""
  lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
  h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
  return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def travel_minutes(a: Building, b: Building, config: Config) -> float:
  """Czas przejścia w minutach = odległość km × routeFactor / walkingSpeedKmh × 60."""
  return distance_km(a, b) * config.route_factor / config.walking_speed_kmh * 60


def travel_matrix(buildings: list[Building], config: Config) -> dict[str, dict[str, float]]:
  """Macierz czasów przejść w minutach (zaokrąglona do 0,1 min), symetryczna, z zerami na przekątnej."""
  return {a.id: {b.id: round(travel_minutes(a, b, config), 1) for b in buildings} for a in buildings}


def min_inner_break(grid: TimeGrid) -> int:
  """Najkrótsza przerwa w środku zajęć, jaka może wystąpić (z uwzględnieniem `breaks.overrides`)."""
  values = [grid.breaks.inner_minutes, *(o.inner_minutes for o in grid.breaks.overrides if o.inner_minutes is not None)]
  return min(values)


def meeting_minutes(units: int, grid: TimeGrid) -> int:
  """Najkrótszy możliwy czas trwania spotkania: jednostki + najkrótsze przerwy w środku.

  Percepcja szuka przeszkód nie do obejścia, więc bierze najkorzystniejszy wariant: jeśli nawet wtedy
  zajęcia się nie mieszczą, nie zmieszczą się nigdy.
  """
  return units * grid.unit_minutes + (units - 1) * min_inner_break(grid)


def day_window(grid: TimeGrid) -> Interval:
  return to_minutes(grid.day_start), to_minutes(grid.day_end)


def allowed_starts(grid: TimeGrid) -> list[int] | None:
  return None if grid.allowed_start_times is None else sorted(to_minutes(t) for t in grid.allowed_start_times)


def fits(window: Interval, duration: int, grid: TimeGrid) -> bool:
  """Czy w oknie mieści się spotkanie z dozwoloną godziną startu (krok `startStepMinutes` od `dayStart`)."""
  start, end = window
  if allowed_starts(grid) is not None:
    return any(start <= s and s + duration <= end for s in allowed_starts(grid))
  base, step = to_minutes(grid.day_start), grid.start_step_minutes
  first = base + math.ceil(max(0, start - base) / step) * step
  return first + duration <= end


def merge(intervals: list[Interval]) -> list[Interval]:
  out: list[Interval] = []
  for s, e in sorted(intervals):
    if out and s <= out[-1][1]:
      out[-1] = (out[-1][0], max(out[-1][1], e))
    elif e > s:
      out.append((s, e))
  return out


def subtract(intervals: list[Interval], cut: Interval) -> list[Interval]:
  out = []
  for s, e in intervals:
    if cut[1] <= s or cut[0] >= e:
      out.append((s, e))
      continue
    if s < cut[0]:
      out.append((s, cut[0]))
    if cut[1] < e:
      out.append((cut[1], e))
  return out


def clip(intervals: list[Interval], window: Interval) -> list[Interval]:
  return [(max(s, window[0]), min(e, window[1])) for s, e in intervals if min(e, window[1]) > max(s, window[0])]


def total(intervals: list[Interval]) -> int:
  return sum(e - s for s, e in intervals)
