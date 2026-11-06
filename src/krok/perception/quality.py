"""Raport jakości danych (#11): co percepcja zebrała, czego nie jest pewna i jak to wpływa na planowanie.

Źródła:
- problemy z importu i dziennik zdarzeń z bazy (#9),
- raport wykonalności (#10) na danych z bazy,
- opcjonalnie manifesty generatora (`_noise.json`, `_events.json`), z których liczymy wykrywalność
  (kryteria, scenariusz „Dane z błędami”: wykryte / wprowadzone).

Kategorie problemów: braki, wartości niepewne, błędy, duplikaty, skutki (odrzucenia pociągnięte przez inne rekordy).
Każde ograniczenie z katalogu w docs/etap2/percepcja.md ma tu miarę (`sensitivity`).
"""

import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select

from krok.db import Issue, read_dataset, read_issues
from krok.db.report import build_report
from krok.db.schema import event_log
from krok.model import Dataset

from .feasibility import FeasibilityReport, analyze

CATEGORIES = ("missing", "uncertain", "errors", "duplicates", "consequences")
LABELS = {"missing": "Braki", "uncertain": "Wartości niepewne", "errors": "Błędy", "duplicates": "Duplikaty",
          "consequences": "Skutki odrzuceń"}

# Rodzaj problemu z importu (#9) → kategoria. `flagged` (rekord w bazie, wartość wątpliwa) zawsze jest „niepewne”.
ISSUE_CATEGORY = {
  "missingField": "missing", "missingFile": "missing",
  "keyTypo": "errors", "unknownField": "errors", "invalidValue": "errors", "outlier": "errors", "badReference": "errors",
  "exactDuplicate": "duplicates", "nearDuplicate": "duplicates", "duplicateId": "duplicates", "duplicateOverride": "duplicates",
  "cascade": "consequences",
}

# Jak import (#9) zgłasza zmianę z `_noise.json` (#7). Zły odwołany id może też odrzucić rekord walidacją albo kaskadą.
NOISE_EXPECTED = {
  "keyTypo": ({"keyTypo"}, {"fixed"}), "missingField": ({"missingField"}, {"rejected", "flagged"}),
  "outlier": ({"outlier"}, {"rejected", "flagged"}), "badReference": ({"badReference", "invalidValue", "cascade"}, {"rejected"}),
  "exactDuplicate": ({"exactDuplicate"}, {"fixed"}), "nearDuplicate": ({"nearDuplicate"}, {"fixed"}),
}
FIELD_CHECKED = {"keyTypo", "missingField", "outlier"}


@dataclass(frozen=True)
class QualityParams:
  """Założenia o niepewności danych. To dane wejściowe raportu, nie stałe: zmienia się je opcjami CLI."""

  coordinate_error_m: float = 30.0  # możliwy błąd współrzędnych budynku
  group_growth: float = 0.10  # o ile grupa może urosnąć po zapisach
  availability_margin: float = 0.8  # wykorzystanie dostępności, od którego kompetencja jest wrażliwa
  examples: int = 3


def issue_category(kind: str, action: str) -> str:
  return "uncertain" if action == "flagged" else ISSUE_CATEGORY.get(kind, "errors")


def event_category(status: str, kind: str | None, message: str | None = None, rejected_ids: frozenset = frozenset()) -> str:
  if status == "duplicate":
    return "duplicates"
  if status == "applied":
    return "uncertain"  # zastosowane mimo problemu, np. spóźniona informacja (outOfOrder)
  if kind == "badReference" and _referenced_id(message) in rejected_ids:
    return "consequences"  # zdarzenie wskazuje rekord, który import odrzucił
  return "missing" if kind == "missingField" else "errors"


def _ids_in(message: str | None) -> set[str]:
  """Całe identyfikatory z komunikatu (bez dopasowań fragmentu, np. P-0001 w P-00011)."""
  return set(re.findall(r"[A-Za-z0-9][\w-]*(?:/[\w-]+)?", message or "")) | {
    part for token in re.findall(r"[\w-]+/[\w-]+", message or "") for part in token.split("/")}


def _referenced_id(message: str | None) -> str | None:
  """Id z komunikatu bazy o złym odwołaniu („nieznane {tabela}/{id}”, #9), np. → P1-004. Id może mieć spacje i „/”."""
  if not message or not message.startswith("nieznane "):
    return None
  return message.removeprefix("nieznane ").partition("/")[2] or None


NO_ID = "(bez id)"


def _ids(issues: list[Issue]) -> list[str]:
  """Posortowane id rekordów z problemów. Rekord bez id (np. z brakującym polem id) też jest dowodem utraty."""
  return sorted({i.record_id or NO_ID for i in issues})


def _norm(field: str | None) -> str:
  return (field or "").replace("_", "").lower()


def _read_events(engine: Engine) -> list[dict]:
  with engine.connect() as conn:
    return [dict(r) for r in conn.execute(select(event_log).order_by(event_log.c.seq)).mappings()]


def _group(items: list[dict], params: QualityParams) -> dict:
  """Liczby i przykłady per kategoria."""
  out = {}
  for cat in CATEGORIES:
    rows = [i for i in items if i["category"] == cat]
    examples = defaultdict(list)
    for r in rows:
      if len(examples[r["kind"]]) < params.examples:
        examples[r["kind"]].append({k: r[k] for k in ("source", "recordId", "action", "field", "message") if k in r})
    out[cat] = {"label": LABELS[cat], "total": len(rows), "byKind": dict(Counter(r["kind"] for r in rows)),
                "byAction": dict(Counter(r["action"] for r in rows)), "examples": dict(examples)}
  return out


# --- wykrywalność ---

def noise_detection(manifest: list[dict], issues: list[Issue], ds: Dataset) -> dict:
  """Ile zmian z `_noise.json` import wykrył. Literówka w odwołaniu, która trafia w inny istniejący id, jest
  niewykrywalna z zasady: rekord jest poprawny, tylko wskazuje coś innego."""
  existing = {x.id for name in ("faculties", "fields", "programs", "groups", "buildings", "rooms", "lecturers", "subjects",
                                "classes") for x in getattr(ds, name)}
  by_kind = defaultdict(lambda: {"introduced": 0, "detected": 0, "undetectable": 0, "masked": 0})
  missed, explained = [], set()
  for change in manifest:
    record = change["after"] if change["kind"] == "nearDuplicate" else change["id"]
    kinds, actions = NOISE_EXPECTED.get(change["kind"], ({change["kind"]}, {"fixed", "rejected", "flagged"}))
    found = [i for i in issues if (i.source, i.record_id) == (change["file"], record) and i.kind in kinds and i.action in actions]
    if change["kind"] in FIELD_CHECKED:
      found = [i for i in found if _norm(i.field) == _norm(change["field"])]
    stats = by_kind[change["kind"]]
    stats["introduced"] += 1
    explained.add((change["file"], record))
    if found:
      stats["detected"] += 1
    elif change["kind"] == "badReference" and change["after"] in existing:
      stats["undetectable"] += 1
    else:
      missed.append(change)
  # Problemy z rekordów, których manifest nie dotyczy, to kandydaci na fałszywe alarmy czyszczenia. Wyjątek to skutki:
  # kaskady oraz rekordy, których komunikat wskazuje rekord zmieniony szumem (np. dziecko grupy z popsutym programId).
  changed_ids = {rid for _, rid in explained}
  def is_consequence(i: Issue) -> bool:
    return i.kind == "cascade" or bool(_ids_in(i.message) & changed_ids)
  other = [i for i in issues if (i.source, i.record_id) not in explained]
  unexplained = [i for i in other if not is_consequence(i)]
  summary = _detection_summary(by_kind, missed, [{"source": i.source, "recordId": i.record_id, "kind": i.kind,
                                                  "message": i.message} for i in unexplained])
  summary["consequencesOfNoise"] = len(other) - len(unexplained)
  return summary


def event_detection(manifest: list[dict], rows: list[dict], rejected_ids: frozenset = frozenset()) -> dict:
  """Ile celowo zepsutych linii strumienia (#8) baza (#9) oznaczyła właściwym rodzajem."""
  by_line = {r["line"]: r for r in rows}
  by_kind = defaultdict(lambda: {"introduced": 0, "detected": 0, "undetectable": 0, "masked": 0})
  missed, masked = [], 0
  for entry in manifest:
    stats = by_kind[entry["kind"]]
    stats["introduced"] += 1
    row = by_line.get(entry["line"])
    if row is not None and row["kind"] == entry["kind"]:
      stats["detected"] += 1
    elif row is not None and event_category(row["status"], row["kind"], row["message"], rejected_ids) == "consequences":
      # Zdarzenie i tak odrzucone, bo wskazuje rekord utracony przy imporcie: jego celowej usterki nie da się już ocenić.
      stats["masked"] += 1
      masked += 1
    else:
      missed.append(entry)
  injected = {e["line"] for e in manifest}
  # Linie spoza manifestu z problemem. Skutki importu (zdarzenie wskazuje rekord odrzucony z danych z szumem)
  # liczymy osobno, bo nie są ani błędem strumienia, ani fałszywym alarmem.
  other = [r for r in rows if r["line"] not in injected and (r["kind"] or r["status"] != "applied")]
  consequences = [r for r in other if event_category(r["status"], r["kind"], r["message"], rejected_ids) == "consequences"]
  unexplained = [{"line": r["line"], "eventId": r["eventId"], "status": r["status"], "kind": r["kind"], "message": r["message"]}
                 for r in other if r not in consequences]
  summary = _detection_summary(by_kind, missed, unexplained)
  summary["consequencesOfImport"] = len(consequences)
  summary["maskedByImport"] = masked
  return summary


def _detection_summary(by_kind, missed, unexplained) -> dict:
  introduced = sum(s["introduced"] for s in by_kind.values())
  detected = sum(s["detected"] for s in by_kind.values())
  undetectable = sum(s["undetectable"] for s in by_kind.values())
  masked = sum(s["masked"] for s in by_kind.values())
  detectable = introduced - undetectable - masked  # niewykrywalne z zasady (L7) i zasłonięte nie wchodzą do mianownika
  return {"introduced": introduced, "detected": detected, "undetectable": undetectable, "masked": masked,
          "recall": round(detected / detectable, 4) if detectable else None, "byKind": dict(by_kind),
          "missed": missed, "notInManifest": len(unexplained), "notInManifestExamples": unexplained[:5]}


def _event_ids(obj) -> set[str]:
  """Id encji, których dotyczy payload zdarzenia (pola `...Id` i `id` zagnieżdżonych encji)."""
  out = set()
  if isinstance(obj, dict):
    for k, v in obj.items():
      if (k.endswith("Id") or k == "id") and isinstance(v, str):
        out.add(v)
      elif k.endswith("Ids") and isinstance(v, list):  # np. scope.groupIds blokady z calendarExceptionAdded
        out |= {x for x in v if isinstance(x, str)}
      else:
        out |= _event_ids(v)
  elif isinstance(obj, list):
    for v in obj:
      out |= _event_ids(v)
  return out


def _changed_by_events(rows: list[dict]) -> set[str]:
  ids = set()
  for r in rows:
    if r["status"] == "applied":
      try:
        ids |= _event_ids(json.loads(r["raw"]).get("payload"))
      except (TypeError, ValueError, AttributeError):
        continue
  return ids


# Co dany rodzaj przeszkody naprawdę sprawdza: od tego zależy, które rekordy mogą być jej przyczyną.
ROOM_CODES = {"brak-sali", "za-malo-sal"}
GROUP_TIME_CODES = {"za-malo-czasu-grupy", "za-duzo-jednostek-grupy", "zablokowana-grupa"}
LECTURER_CODES = {"brak-kompetencji", "przydzial-bez-kompetencji", "brak-dostepnego-prowadzacego", "za-malo-czasu-prowadzacego",
                  "za-malo-czasu-puli", "za-malo-czasu-kompetencji", "limit-prowadzacego", "limit-puli", "limit-kompetencji"}


def _related(problem, state) -> set[str]:
  """Rekordy, od których przeszkoda zależy wprost.

  brak-sali: grupa (jej liczebność). Plan studenta: grupa, jej przodkowie i ich zajęcia. Obsada: przedmiot, zajęcia
  i prowadzący. Inne: zajęcia i przedmiot.
  """
  related = set(problem.ids)
  if problem.code == "brak-sali":
    return related
  if problem.code in GROUP_TIME_CODES:
    for gid in problem.ids:
      related |= set(state.ancestors.get(gid, []))
    return related | set(problem.class_ids)
  for cid in problem.class_ids:
    info = state.classes.get(cid)
    if info:
      related |= {cid, info.cls.subject_id}
  return related


def _feasibility(report: FeasibilityReport, state, issues: list[Issue], rows: list[dict]) -> dict:
  """Wynik #10 i przyczyny przeszkód:
  - dataQuality: dotyczy rekordu odrzuconego, oznaczonego albo zmienionego kaskadą przy imporcie,
  - events: dotyczy rekordu zmienionego zdarzeniem,
  - lostRecords: import odrzucił rekordy zasobu, od którego przeszkoda zależy (prowadzących albo sal). Raport z bazy
    zna tylko id odrzuconych rekordów, a nie ich treść, więc to przyczyna prawdopodobna, a nie pewna,
  - unexplained: żadne z powyższych.
  """
  touched = {i.record_id for i in issues if i.record_id and (i.action in ("rejected", "flagged") or i.kind == "cascade")}
  lost = {"lecturers": _ids([i for i in issues if i.source == "lecturers.json" and i.action == "rejected"]),
          "rooms": _ids([i for i in issues if i.source == "rooms.json" and i.action == "rejected"])}
  changed = _changed_by_events(rows)
  causes = {"dataQuality": [], "events": [], "lostRecords": [], "unexplained": []}
  for p in report.problems:
    if p.severity != "error":
      continue
    related = _related(p, state)
    entry = {"code": p.code, "message": p.message}
    resource = "rooms" if p.code in ROOM_CODES else "lecturers" if p.code in LECTURER_CODES else None
    if related & touched:
      causes["dataQuality"].append(entry | {"records": sorted(related & touched)[:5]})
    elif related & changed:
      causes["events"].append(entry | {"records": sorted(related & changed)[:5]})
    elif resource and lost[resource]:
      causes["lostRecords"].append(entry | {"records": lost[resource][:5]})
    else:
      causes["unexplained"].append(entry | {"records": []})
  return {"verdict": report.verdict, "errors": report.errors, "warnings": report.warnings,
          "byCode": dict(Counter(p.code for p in report.problems)),
          "causes": {k: len(v) for k, v in causes.items()}, "causeExamples": {k: v[:5] for k, v in causes.items() if v}}


# --- wrażliwość na ograniczenia percepcji (katalog w docs/etap2/percepcja.md) ---

def _coordinates(ds: Dataset, params: QualityParams, travel: dict) -> dict:
  """L1: przybliżone współrzędne. Błąd e metrów w obu budynkach zmienia odległość o ≤ 2e."""
  cfg = ds.config
  delta = 2 * params.coordinate_error_m / 1000 * cfg.route_factor / cfg.walking_speed_kmh * 60
  ids = sorted(travel)
  pairs = [{"from": a, "to": b, "minutes": travel[a][b]} for i, a in enumerate(ids) for b in ids[i + 1:]]
  sensitive = [p for p in pairs if p["minutes"] and delta / p["minutes"] >= 0.2]
  return {"coordinateErrorM": params.coordinate_error_m, "travelErrorMinutes": round(delta, 2),
          "maxTravelMinutes": max((p["minutes"] for p in pairs), default=0), "pairs": len(pairs),
          "pairsOver20PercentError": len(sensitive),
          "minBetweenClassesMinutes": cfg.time_grid.breaks.min_between_classes_minutes}


def _availability(report: FeasibilityReport, params: QualityParams) -> dict:
  """L2: deklarowana dostępność może być większa niż faktyczna. Kompetencja blisko granicy jest wrażliwa."""
  rows = []
  for b in report.balance.competences:
    if b.available_minutes:
      use = b.demand_minutes / b.available_minutes
      rows.append({"competence": b.competence, "utilization": round(use, 3),
                   "maxAvailabilityLoss": round(max(0.0, 1 - use), 3)})
  rows.sort(key=lambda r: -r["utilization"])
  risky = [r for r in rows if r["utilization"] >= params.availability_margin]
  return {"margin": params.availability_margin, "competences": len(rows), "atRisk": len(risky), "examples": rows[:params.examples]}


def _group_growth(ds: Dataset, state, params: QualityParams) -> dict:
  """L3: liczebność grup szacowana przed zapisami. Zajęcia, które stracą salę, gdy grupa urośnie o group_growth."""
  at_risk = []
  for info in state.classes.values():
    rooms = state.rooms_by_type.get(info.cls.room_type, [])
    biggest = rooms[0].capacity if rooms else 0
    grown = math.ceil(info.group.size * (1 + params.group_growth))
    if info.group.size <= biggest < grown:
      at_risk.append({"classId": info.cls.id, "groupId": info.group.id, "size": info.group.size, "grown": grown,
                      "biggestRoom": biggest, "roomType": info.cls.room_type})
  return {"growth": params.group_growth, "classes": len(state.classes), "atRisk": len(at_risk),
          "groups": len({r["groupId"] for r in at_risk}), "examples": at_risk[:params.examples]}


def _room_capacity(issues: list[Issue]) -> dict:
  """L4: brak albo zła pojemność sali. Odrzucona sala jest nieużywalna, a oznaczona grozi przepełnieniem."""
  hit = [i for i in issues if i.source == "rooms.json" and _norm(i.field) == "capacity"]
  return {"rejected": _ids([i for i in hit if i.action == "rejected"]), "flagged": _ids([i for i in hit if i.action == "flagged"])}


def _heuristics(issues: list[Issue], params: QualityParams) -> dict:
  """L5: progi czyszczenia (#9). Wartości oznaczone jako odstające zostały w bazie, choć mogą być błędne."""
  flagged = [i for i in issues if i.action == "flagged"]
  return {"flagged": len(flagged), "examples": [{"source": i.source, "recordId": i.record_id, "message": i.message}
                                                for i in flagged[:params.examples]]}


def _effective_later(rows: list[dict]) -> dict:
  """L6: baza trzyma jeden stan bez historii, więc zdarzenie zmienia go od razu, także gdy obowiązuje później."""
  ahead = []
  for r in rows:
    if r["status"] != "applied":
      continue
    try:
      raw = json.loads(r["raw"])
      known = dt.datetime.fromisoformat(raw["timestamp"]).date()
      payload = raw["payload"]
      start = payload.get("from") or payload.get("effectiveFrom") or (payload.get("exception") or {}).get("from")
      if start and dt.date.fromisoformat(start) > known:
        ahead.append((dt.date.fromisoformat(start) - known).days)
    except (KeyError, TypeError, ValueError):
      continue
  return {"applied": sum(r["status"] == "applied" for r in rows), "takeEffectLater": len(ahead),
          "maxDaysAhead": max(ahead, default=0)}


# --- raport ---

def build_quality_report(engine: Engine, *, noise_manifest: list[dict] | None = None, events_manifest: list[dict] | None = None,
                         params: QualityParams = QualityParams()) -> dict:
  """Raport jakości dla bazy po imporcie (i zdarzeniach). Manifesty są opcjonalne: bez nich nie ma wykrywalności."""
  base = build_report(engine)
  issues = read_issues(engine)
  rows = _read_events(engine)
  ds = read_dataset(engine)
  state, feasibility = analyze(ds)

  issue_items = [{"category": issue_category(i.kind, i.action), "source": i.source, "recordId": i.record_id,
                  "kind": i.kind, "action": i.action, "field": i.field, "message": i.message} for i in issues]
  rejected_ids = frozenset(i.record_id for i in issues if i.action == "rejected" and i.record_id)
  event_items = [{"category": event_category(r["status"], r["kind"], r["message"], rejected_ids), "source": f"linia {r['line']}",
                  "recordId": r["eventId"],
                  "kind": r["kind"] or r["status"], "action": r["status"], "message": r["message"] or ""}
                 for r in rows if r["kind"] or r["status"] != "applied"]

  report = {
    "records": base["records"],
    "imported": {"issues": len(issues), "byAction": dict(Counter(i.action for i in issues)),
                 "categories": _group(issue_items, params)},
    "events": {"lines": len(rows), "byStatus": dict(Counter(r["status"] for r in rows)),
               "categories": _group(event_items, params)},
    "feasibility": _feasibility(feasibility, state, issues, rows),
    "sensitivity": {
      "coordinates": _coordinates(ds, params, state.travel),
      "availability": _availability(feasibility, params),
      "groupGrowth": _group_growth(ds, state, params),
      "roomCapacity": _room_capacity(issues),
      "heuristics": _heuristics(issues, params),
      "eventsBeforeEffectiveDate": _effective_later(rows),
    },
    "detection": {},
  }
  if noise_manifest is not None:
    report["detection"]["noise"] = noise_detection(noise_manifest, issues, ds)
  if events_manifest is not None:
    report["detection"]["events"] = event_detection(events_manifest, rows, rejected_ids)
  return report


class ManifestError(ValueError):
  """Manifest generatora nie istnieje (a został podany wprost), jest zepsuty albo ma zły kształt."""


def _read_manifest(path: Path | None, required: bool, unwrap, keys: set[str]) -> list | None:
  if path is None or (not path.exists() and not required):
    return None  # domyślny manifest jest opcjonalny: bez niego nie ma wykrywalności
  if not path.exists():
    raise ManifestError(f"Nie ma manifestu {path}")
  try:
    entries = unwrap(json.loads(path.read_text(encoding="utf-8")))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
    raise ManifestError(f"Nie można wczytać manifestu {path}: {e}") from e
  if not isinstance(entries, list) or not all(isinstance(e, dict) and keys <= set(e) for e in entries):
    raise ManifestError(f"Manifest {path} ma zły kształt: oczekiwana lista wpisów z polami {', '.join(sorted(keys))}")
  return entries


def load_manifests(noise_path: Path | None, events_path: Path | None, *, noise_required: bool = False,
                   events_required: bool = False) -> tuple[list | None, list | None]:
  """Czyta `_noise.json` (lista albo {"changes": [...]}) i `_events.json` (`errorsList`).

  Brak pliku daje None, chyba że ścieżka jest wymagana (podana wprost). Zepsuty plik albo zły kształt → ManifestError.
  """
  noise = _read_manifest(noise_path, noise_required, lambda d: d.get("changes") if isinstance(d, dict) else d,
                         {"file", "id", "kind", "field", "after"})
  events = _read_manifest(events_path, events_required, lambda d: d.get("errorsList") if isinstance(d, dict) else None,
                          {"line", "kind"})
  return noise, events


def _pct(x) -> str:
  return "—" if x is None else f"{x:.0%}"


def render_markdown(report: dict) -> str:
  """Raport jakości jako Markdown (do repo, PR albo sprawozdania)."""
  out = ["# Raport jakości danych", ""]
  rec = report["records"]
  out += ["## Dane w bazie", "", "| " + " | ".join(rec) + " |", "|" + "---|" * len(rec), "| " + " | ".join(map(str, rec.values())) + " |", ""]

  for title, section in (("Problemy z importu", report["imported"]), ("Problemy ze zdarzeń", report["events"])):
    out += [f"## {title}", "", "| Kategoria | Liczba | Rodzaje |", "|---|---|---|"]
    for cat in section["categories"].values():
      kinds = ", ".join(f"`{k}` {n}" for k, n in sorted(cat["byKind"].items(), key=lambda kv: -kv[1])) or "—"
      out.append(f"| {cat['label']} | {cat['total']} | {kinds} |")
    out.append("")
    for cat in section["categories"].values():
      for kind, examples in cat["examples"].items():
        out += [f"- **{cat['label']} / `{kind}`**: " + "; ".join(
          f"{e['source']} `{e.get('recordId') or '-'}`: {e['message']}" for e in examples)]
    out.append("")

  if report["detection"]:
    out += ["## Wykrywalność (wykryte / wprowadzone)", "",
            "| Źródło | Rodzaj | Wprowadzone | Wykryte | Niewykrywalne z zasady | Zasłonięte przez skutek importu |",
            "|---|---|---|---|---|---|"]
    for name, det in report["detection"].items():
      for kind, s in sorted(det["byKind"].items()):
        out.append(f"| {name} | `{kind}` | {s['introduced']} | {s['detected']} | {s['undetectable']} | {s['masked']} |")
      out.append(f"| **{name}** | **razem** | **{det['introduced']}** | **{det['detected']}** | **{det['undetectable']}** | "
                 f"**{det['masked']}** |")
    out.append("")
    for name, det in report["detection"].items():
      extra = (f", skutki importu {det['consequencesOfImport']}, usterki zasłonięte przez skutek importu {det['maskedByImport']}"
               if "consequencesOfImport" in det else f", skutki szumu (kaskady i rekordy zależne) {det.get('consequencesOfNoise', 0)}")
      out.append(f"- {name}: wykrywalność {_pct(det['recall'])}, przeoczone {len(det['missed'])}, "
                 f"problemy spoza manifestu {det['notInManifest']}{extra}")
    out.append("")

  f = report["feasibility"]
  c = f["causes"]
  out += ["## Wykonalność (#10)", "", f"Werdykt: `{f['verdict']}`, błędy {f['errors']}, ostrzeżenia {f['warnings']}.", "",
          "| Przyczyna przeszkody | Liczba |", "|---|---|",
          f"| rekord z problemem z importu | {c['dataQuality']} |", f"| rekord zmieniony zdarzeniem | {c['events']} |",
          f"| prawdopodobnie odrzucone rekordy prowadzących lub sal | {c['lostRecords']} |",
          f"| bez znanej przyczyny w danych | {c['unexplained']} |", ""]
  for cause, label in (("dataQuality", "rekordy z problemami"), ("events", "zmienione zdarzeniem"),
                       ("lostRecords", "odrzucone przy imporcie"), ("unexplained", "")):
    for x in f["causeExamples"].get(cause, []):
      out.append(f"- `{x['code']}`: {x['message']}" + (f" ({label}: {', '.join(x['records'])})" if x["records"] else ""))
  out.append("")

  s = report["sensitivity"]
  c, a, g, rc, h, e = (s[k] for k in ("coordinates", "availability", "groupGrowth", "roomCapacity", "heuristics",
                                       "eventsBeforeEffectiveDate"))
  out += ["## Wrażliwość na ograniczenia percepcji", "", "| | Ograniczenie | Miara |", "|---|---|---|",
          f"| L1 | współrzędne przybliżone (±{c['coordinateErrorM']:g} m) | czas przejścia ±{c['travelErrorMinutes']} min; "
          f"{c['pairsOver20PercentError']} z {c['pairs']} par budynków z błędem ≥ 20% |",
          f"| L2 | deklarowana dostępność ≠ faktyczna | {a['atRisk']} z {a['competences']} kompetencji wykorzystuje "
          f"≥ {a['margin']:.0%} dostępności |",
          f"| L3 | liczebność grup szacowana (+{g['growth']:.0%}) | {g['atRisk']} zajęć ({g['groups']} grup) traci salę |",
          f"| L4 | brak lub zła pojemność sali | odrzucone sale: {len(rc['rejected'])}, wątpliwe: {len(rc['flagged'])} |",
          f"| L5 | progi czyszczenia | {h['flagged']} wątpliwych wartości zostało w bazie |",
          f"| L6 | stan bez historii | {e['takeEffectLater']} z {e['applied']} zdarzeń obowiązuje później, "
          f"niż zmienia stan (do {e['maxDaysAhead']} dni) |", ""]
  return "\n".join(out)


def render_text(report: dict) -> str:
  """Krótkie podsumowanie do terminala."""
  lines = []
  for title, section in (("Import", report["imported"]), ("Zdarzenia", report["events"])):
    parts = [f"{c['label'].lower()} {c['total']}" for c in section["categories"].values() if c["total"]]
    lines.append(f"{title}: " + (", ".join(parts) or "bez problemów"))
  for name, det in report["detection"].items():
    extra = (f", skutki importu {det['consequencesOfImport']} (zasłonięte {det['maskedByImport']})"
             if "consequencesOfImport" in det else f", skutki szumu {det.get('consequencesOfNoise', 0)}")
    lines.append(f"Wykrywalność ({name}): {det['detected']}/{det['introduced'] - det['undetectable'] - det['masked']}"
                 f" = {_pct(det['recall'])}"
                 f", niewykrywalne z zasady {det['undetectable']}, spoza manifestu {det['notInManifest']}{extra}")
  f = report["feasibility"]
  c = f["causes"]
  lines.append(f"Wykonalność: {f['verdict']} ({f['errors']} błędów, {f['warnings']} ostrzeżeń; przyczyna: import "
               f"{c['dataQuality']}, zdarzenia {c['events']}, prawdopodobnie odrzucone rekordy {c['lostRecords']}, "
               f"nieznana {c['unexplained']})")
  g, a = report["sensitivity"]["groupGrowth"], report["sensitivity"]["availability"]
  lines.append(f"Ryzyka: {g['atRisk']} zajęć bez sali przy +{g['growth']:.0%} studentów, "
               f"{a['atRisk']} kompetencji z dostępnością wykorzystaną ≥ {a['margin']:.0%}")
  return "\n".join(lines)
