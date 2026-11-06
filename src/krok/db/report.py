"""Raport z importu i zdarzeń: liczby i przykłady. Podstawa raportu jakości z #11."""

from collections import Counter, defaultdict

from sqlalchemy import Engine, func, select

from .schema import TABLES, event_log, read_issues

EXAMPLES = 3


def build_report(engine: Engine) -> dict:
  issues = read_issues(engine)
  examples: dict[str, list[dict]] = defaultdict(list)
  for i in issues:
    if len(examples[i.kind]) < EXAMPLES:
      examples[i.kind].append({"source": i.source, "recordId": i.record_id, "action": i.action, "message": i.message})
  with engine.connect() as conn:
    records = {name: conn.execute(select(func.count()).select_from(t)).scalar_one() for name, t in TABLES.items()}
    logged = conn.execute(select(event_log.c.status, event_log.c.kind)).all()
  return {
    "records": records,
    "issues": {"total": len(issues), "byKind": dict(Counter(i.kind for i in issues)), "byAction": dict(Counter(i.action for i in issues)),
               "examples": dict(examples)},
    "events": {"total": len(logged), "byStatus": dict(Counter(r.status for r in logged)),
               "byKind": dict(Counter(r.kind for r in logged if r.kind))},
  }


def format_report(report: dict) -> str:
  lines = ["Rekordy w bazie:"]
  lines += [f"  {name:10} {count}" for name, count in report["records"].items()]
  issues = report["issues"]
  lines.append(f"\nProblemy z importu: {issues['total']} {issues['byAction'] or ''}".rstrip())
  for kind, count in sorted(issues["byKind"].items(), key=lambda kv: -kv[1]):
    lines.append(f"  {kind:15} {count}")
    lines += [f"    {e['source']} {e['recordId'] or '-'}: {e['message']}" for e in issues["examples"].get(kind, [])]
  events = report["events"]
  lines.append(f"\nZdarzenia: {events['total']} {events['byStatus'] or ''}".rstrip())
  lines += [f"  {kind:15} {count}" for kind, count in sorted(events["byKind"].items(), key=lambda kv: -kv[1])]
  return "\n".join(lines)
