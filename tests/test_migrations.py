"""
test_migrations.py — schema versioning + ordered migrations
(merchant_intelligence/migrations.py), fully hermetic on temp databases.

Covers:
  1. Fresh DB -> all migrations apply in order, user_version stamped.
  2. Idempotency — re-running applies nothing and keeps the version.
  3. Partial upgrade — a v1-stamped DB only receives the later migrations.
  4. Failure isolation — a failing migration leaves the version stamp
     untouched (the next run retries it) and reports the error.
  5. Newer-DB guard — a DB at a version above the registry is skipped,
     never downgraded.
  6. Missing DB file is reported, not created.
  7. apply_all aggregates per-DB results; versions() reads stamps back.
  8. Real data-platform tables exist after the shipped registry applies
     (source_files present -> ingestion.py's CDC stops failing with
     "no such table").

Run:  python -X utf-8 tests/test_migrations.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from merchant_intelligence import migrations as m

checks = 0
fails = 0


def check(name, cond, detail=""):
    global checks, fails
    checks += 1
    mark = "ok" if cond else "FAIL"
    if not cond:
        fails += 1
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


TMP = Path(tempfile.mkdtemp())


def make_db(name="t.db", with_merchants=True):
    p = TMP / name
    conn = sqlite3.connect(str(p))
    if with_merchants:
        conn.execute("CREATE TABLE merchants (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return p


def version_of(p):
    conn = sqlite3.connect(str(p))
    try:
        return m.get_version(conn)
    finally:
        conn.close()


# ── 1. fresh DB: full upgrade ────────────────────────────────────────────
print("== 1. fresh DB upgrade ==")
db = make_db()
r = m.apply_migrations(db)
check("upgrade ok", r["ok"] is True, str(r))
check("all migrations applied", r["applied"] == [1, 2, 3], str(r["applied"]))
check("version stamped to latest", version_of(db) == m.LATEST_VERSION)
check("source_files table exists",
      _t := sqlite3.connect(str(db)).execute(
          "SELECT name FROM sqlite_master WHERE type='table' "
          "AND name='source_files'").fetchone() is not None)
check("identifiers table exists",
      sqlite3.connect(str(db)).execute(
          "SELECT name FROM sqlite_master WHERE type='table' "
          "AND name='identifiers'").fetchone() is not None)

# ── 2. idempotency ───────────────────────────────────────────────────────
print("== 2. idempotency ==")
r = m.apply_migrations(db)
check("second run applies nothing", r["ok"] and r["applied"] == [], str(r))
check("version unchanged", version_of(db) == m.LATEST_VERSION)

# ── 3. partial upgrade ───────────────────────────────────────────────────
print("== 3. partial upgrade ==")
db2 = make_db("partial.db")
conn = sqlite3.connect(str(db2))
conn.execute("PRAGMA user_version = 1")
conn.commit()
conn.close()
r = m.apply_migrations(db2)
check("v1-stamped DB receives v2 and v3",
      r["applied"] == [2, 3] and version_of(db2) == 3, str(r))

# ── 4. failure isolation ─────────────────────────────────────────────────
print("== 4. failure isolation ==")
db3 = make_db("fail.db")
bad = [
    (1, "ok migration", "SELECT 1;"),
    (2, "broken migration", "CREATE TABLE this is not sql;"),
    (3, "never reached", "SELECT 1;"),
]
r = m.apply_migrations(db3, bad)
check("failing migration reported", r["ok"] is False and "error" in r,
      str(r))
# v1 succeeded before v2 failed, so the stamp sits at the last GOOD migration
check("version stamp at last good migration (retry-safe)",
      version_of(db3) == 1, str(version_of(db3)))
check("earlier migration still applied",
      r["applied"] == [1], str(r["applied"]))
# retry with the registry fixed -> completes
good = [(1, "ok migration", "SELECT 1;"),
        (2, "fixed", "CREATE TABLE fixed_table (id INTEGER);"),
        (3, "third", "SELECT 1;")]
r = m.apply_migrations(db3, good)
check("retry after fix completes", r["ok"] and version_of(db3) == 3, str(r))

# ── 5. newer-DB guard ────────────────────────────────────────────────────
print("== 5. newer-DB guard ==")
db4 = make_db("newer.db")
conn = sqlite3.connect(str(db4))
conn.execute("PRAGMA user_version = 99")
conn.commit()
conn.close()
r = m.apply_migrations(db4)
check("newer DB skipped, not downgraded",
      r["ok"] is True and version_of(db4) == 99 and r["applied"] == [],
      str(r))
check("skip reason reported", "newer than code" in r.get("skipped_reason", ""))

# ── 6. missing DB ────────────────────────────────────────────────────────
print("== 6. missing DB ==")
r = m.apply_migrations(TMP / "nope.db")
check("missing DB reported, not created",
      r["ok"] is False and not (TMP / "nope.db").exists())

# ── 7. apply_all + versions ──────────────────────────────────────────────
print("== 7. apply_all + versions ==")
paths = [make_db("all1.db"), make_db("all2.db"), TMP / "absent.db"]
r = m.apply_all(paths)
check("apply_all ok=True with one missing DB", r["ok"] is True, str(r))
check("two upgraded, one reported",
      sum(1 for x in r["results"] if x.get("applied")) == 2
      and any("does not exist" in x.get("skipped_reason", "")
              for x in r["results"]), str(r))
v = m.versions(paths)
check("versions reads stamps back",
      v["all1.db"] == m.LATEST_VERSION and v["absent.db"] is None, str(v))

# ── 8. shipped registry on a realistic DB ────────────────────────────────
print("== 8. shipped registry ==")
db5 = make_db("shipped.db")
r = m.apply_migrations(db5, m.MIGRATIONS)
check("shipped registry applies cleanly", r["ok"] is True, str(r))
tables = {row[0] for row in sqlite3.connect(str(db5)).execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
check("all four data-platform tables created",
      {"source_files", "identifiers", "entity_clusters",
       "data_quality_log"} <= tables, str(sorted(tables)))
check("auth/encryption tables NOT auto-created (opt-in endpoint only)",
      not ({"app_users", "encryption_keys"} & tables), str(sorted(tables)))

print()
print(f"RESULT: {checks - fails}/{checks} checks passed")
sys.exit(1 if fails else 0)
