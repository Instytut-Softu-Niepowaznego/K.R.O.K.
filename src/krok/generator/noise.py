"""Szum w danych do testowania percepcji (#9, #11): braki, literówki, złe odwołania, wartości odstające, duplikaty.

Działa na gotowych plikach JSON, więc wynik celowo nie przechodzi walidacji modelu.
Każda zmiana jest zapisana w manifeście (_noise.json), żeby dało się zmierzyć, ile z nich wykryła percepcja.
"""

import copy
import random

from .settings import NoiseSpec

NOISY_FILES = ["groups.json", "rooms.json", "lecturers.json", "subjects.json", "classes.json", "buildings.json"]
NUMERIC = ["size", "capacity", "maxUnitsWeekly", "unitsPerWeek", "lat", "lon"]


def _typo(text: str, rng: random.Random) -> str:
  if len(text) < 3:
    return text + text[-1]
  i = rng.randrange(1, len(text) - 1)
  return rng.choice([
    text[:i] + text[i + 1:],                          # brakująca litera
    text[:i] + text[i + 1] + text[i] + text[i + 2:],  # zamienione litery
    text[0].upper() + text[1:],                       # wielka litera
  ])


def _missing(rec: dict, rng: random.Random):
  keys = [k for k, v in rec.items() if k != "id" and v is not None]
  if not keys:
    return None
  k = rng.choice(keys)
  return k, rec.pop(k), None


def _key_typo(rec: dict, rng: random.Random):
  keys = [k for k in rec if k != "id"]
  k = rng.choice(keys)
  new = _typo(k, rng)
  if new == k or new in rec:
    return None
  rec[new] = rec.pop(k)
  return k, k, new


def _bad_reference(rec: dict, rng: random.Random):
  keys = [k for k, v in rec.items() if k.endswith("Id") and isinstance(v, str)]
  if not keys:
    return None
  k = rng.choice(keys)
  before = rec[k]
  rec[k] = _typo(before, rng) if rng.random() < 0.5 else before + "X"
  return (k, before, rec[k]) if rec[k] != before else None


def _outlier(rec: dict, rng: random.Random):
  keys = [k for k in NUMERIC if isinstance(rec.get(k), (int, float))]
  if not keys:
    return None
  k = rng.choice(keys)
  before = rec[k]
  rec[k] = rng.choice([0, -before, before * 10]) if k not in ("lat", "lon") else before + rng.choice([-5, 5])
  return k, before, rec[k]


def apply_noise(files: dict[str, object], spec: NoiseSpec, rng: random.Random) -> tuple[dict[str, object], list[dict]]:
  files = copy.deepcopy(files)
  manifest: list[dict] = []
  kinds = [("missingField", spec.missing_field_rate, _missing), ("keyTypo", spec.key_typo_rate, _key_typo),
           ("badReference", spec.bad_reference_rate, _bad_reference), ("outlier", spec.outlier_rate, _outlier)]

  for name in NOISY_FILES:
    records = files[name]
    extra = []
    for rec in records:
      rid = rec["id"]
      r = rng.random()
      acc = 0.0
      for kind, rate, fn in kinds:
        acc += rate
        if r < acc:
          change = fn(rec, rng)
          if change:
            manifest.append({"file": name, "id": rid, "kind": kind, "field": change[0], "before": change[1], "after": change[2]})
          break
      else:
        if r < acc + spec.duplicate_rate:
          dup = copy.deepcopy(rec)
          exact = rng.random() < 0.5
          if not exact:
            dup["id"] = f"{rid}-2"
          extra.append(dup)
          manifest.append({"file": name, "id": rid, "kind": "exactDuplicate" if exact else "nearDuplicate",
                           "field": "id", "before": rid, "after": dup["id"]})
    records.extend(extra)
  return files, manifest
