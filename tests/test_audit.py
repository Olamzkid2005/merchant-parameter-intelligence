"""
test_audit.py — Append-only audit trail (docs/technical-review-2026-08-original.md #1).

Hermetic: MERCHANT_AUDIT_DB points at a temp SQLite file, so the shipped
data/audit_log.db is never touched. Covers bootstrap, record, filters,
stats, ordering, and the append-only invariant (ids strictly increase;
the module exposes no update/delete path).
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {info}")


_tmp = tempfile.mkdtemp(prefix="audit_")
_db = str(Path(_tmp) / "audit_log.db")
os.environ["MERCHANT_AUDIT_DB"] = _db

from merchant_intelligence import audit  # noqa: E402

print("\n[1] bootstrap + record")
check("empty DB: recent() is []", audit.recent() == [])
check("empty DB: stats zeroed",
      audit.stats()["total"] == 0 and audit.stats()["by_action"] == {})
audit.record("search", scope='{"query": "MEDPLUS"}')
audit.record("profile", scope='{"query": "LAGOON WATERS"}', actor="analyst")
audit.record("export", scope='{"kind": "task"}')
rows = audit.recent()
check("record appends entries", len(rows) == 3, repr(rows))
check("entry carries ts/actor/action/scope",
      rows[-1]["action"] == "search" and rows[-1]["scope"] == '{"query": "MEDPLUS"}'
      and rows[-1]["actor"] == "local", repr(rows[-1]))
check("newest first", rows[0]["action"] == "export", repr(rows))
check("ids strictly increase (append-only)",
      all(rows[i]["id"] > rows[i + 1]["id"] for i in range(len(rows) - 1)),
      repr([r["id"] for r in rows]))

print("\n[2] filters + stats")
check("filter by action", [r["action"] for r in audit.recent(action="search")] == ["search"])
check("filter by actor",
      all(r["actor"] == "analyst" for r in audit.recent(actor="analyst"))
      and len(audit.recent(actor="analyst")) == 1)
check("limit respected", len(audit.recent(limit=2)) == 2)
st = audit.stats()
check("stats totals", st["total"] == 3, repr(st))
check("stats by_action",
      st["by_action"].get("search") == 1 and st["by_action"].get("export") == 1,
      repr(st["by_action"]))
check("stats last_24h counts everything (fresh writes)",
      st["last_24h"] == 3, repr(st))
check("stats newest is set", bool(st["newest"]), repr(st))

print("\n[3] append-only by construction")
import inspect  # noqa: E402
source = inspect.getsource(audit)
for banned in ("UPDATE audit_log", "DELETE FROM audit_log", "DROP TABLE"):
    check(f"module has no {banned}", banned not in source)
public = {n for n in dir(audit) if not n.startswith("_")}
check("only write path is record()",
      not (public & {"update", "delete", "remove", "truncate", "drop"}), repr(public))

print("\n[4] resilience")
os.environ["MERCHANT_AUDIT_DB"] = str(Path(_tmp) / "missing_dir" / "audit.db")
audit.record("task", scope='{"text": "x"}')
check("record creates missing parent dir", len(audit.recent()) == 1)
os.environ["MERCHANT_AUDIT_DB"] = _db

print("\n============================================================")
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("============================================================")
sys.exit(1 if FAIL else 0)
