"""
test_lineage.py — source lineage (merchant_intelligence/lineage.py +
the v3 migration), fully hermetic on temp databases.

Covers:
  1. v3 migration — adds merchants.source_file_id + index; backfills from
     source_files via "<file stem> :: <sheet>" matching; idempotent.
  2. merchant_trace — resolves via source_file_id ("id" link), falls back to
     the sheet_name join ("sheet_name" link), reports "none" when untraceable.
  3. file_summary — per-file rows/merchants/sheet breakdown, traced totals.
  4. file_trace — matches by file name / stem fragment; sample merchants;
     clean no-match error.

Run:  python -X utf-8 tests/test_lineage.py
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
from merchant_intelligence import lineage

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


def make_db(name="lin.db"):
    """Fresh DB at the build-script baseline: merchants WITHOUT the lineage
    column (v3 must add it) and no source_files table (v2 must create it)."""
    p = TMP / name
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE merchants (id INTEGER PRIMARY KEY, sheet_name TEXT,"
        " row_number INTEGER, merchant_name TEXT, merchant_id TEXT,"
        " tid TEXT, mxcode TEXT, imported_at TEXT)")
    conn.commit()
    conn.close()
    return p


def seed_source_file(db, file_path, sheet, rows=2):
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "INSERT INTO source_files (file_path, file_hash, sheet_name,"
        " row_count, column_names, ingested_at, status)"
        " VALUES (?, 'abcd1234ef567890', ?, ?, '[]', '2026-08-26T10:00:00', 'ok')",
        (str(file_path), sheet, rows))
    conn.commit()
    sf_id = cur.lastrowid
    conn.close()
    return sf_id


def add_merchant(db, sheet_name, row_number, name, sf_id=None):
    conn = sqlite3.connect(str(db))
    # source_file_id only when explicitly stamped — pre-v3 rows exist without
    # the column, so the INSERT must not name it unconditionally.
    if sf_id is not None:
        cur = conn.execute(
            "INSERT INTO merchants (sheet_name, row_number, merchant_name,"
            " merchant_id, tid, mxcode, imported_at, source_file_id)"
            " VALUES (?, ?, ?, 'MID1', '2ISWTEST1', 'MXTEST1',"
            " '2026-08-26T10:00:00', ?)",
            (sheet_name, row_number, name, sf_id))
    else:
        cur = conn.execute(
            "INSERT INTO merchants (sheet_name, row_number, merchant_name,"
            " merchant_id, tid, mxcode, imported_at)"
            " VALUES (?, ?, ?, 'MID1', '2ISWTEST1', 'MXTEST1',"
            " '2026-08-26T10:00:00')",
            (sheet_name, row_number, name))
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


# ── 1. v3 migration ──────────────────────────────────────────────────────
print("== 1. v3 source-lineage migration ==")
db = make_db()
# Bring the DB to v2 (creates source_files) so we can seed lineage data,
# then let v3 run against it — proving the backfill finds real rows.
r = m.apply_migrations(db, m.MIGRATIONS[:2])
check("v1+v2 applied", r["ok"] and r["to_version"] == 2, str(r))
seed_source_file(db, TMP / "My Workbook.xlsx", "Sheet1")
# A merchant that already exists when v3 runs — the backfill must stamp it.
add_merchant(db, "My Workbook :: Sheet1", 5, "ACME LTD")

r = m.apply_migrations(db)
check("migrations apply cleanly", r["ok"] and r["to_version"] == 3, str(r))

conn = sqlite3.connect(str(db))
cols = {row[1] for row in conn.execute("PRAGMA table_info(merchants)")}
check("merchants.source_file_id column added", "source_file_id" in cols)
idx = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND "
    "name='idx_merchants_source_file'").fetchone()
check("lineage index created", idx is not None)

# Backfill: the PRE-v3 merchant (id 1) got stamped by stem :: sheet match
conn = sqlite3.connect(str(db))
got = conn.execute(
    "SELECT source_file_id FROM merchants WHERE id = 1").fetchone()[0]
check("backfill stamps rows via stem :: sheet match",
      got == 1, f"source_file_id={got}")
conn.close()

# Post-v3 merchant + build-style stamping (what build_intelligence_db does
# per sheet) -> the trace 'id' link path.
mid = add_merchant(db, "My Workbook :: Sheet1", 7, "ACME TWO LTD")
conn = sqlite3.connect(str(db))
conn.execute("UPDATE merchants SET source_file_id = 1 WHERE id = ?", (mid,))
conn.commit()
conn.close()
unmatched = add_merchant(db, "Unknown File :: Sheet9", 2, "GHOST LTD")

# Idempotent re-run
r = m.apply_migrations(db)
check("re-run is idempotent", r["ok"] and r["applied"] == [], str(r))

# ── 2. merchant_trace ────────────────────────────────────────────────────
print("== 2. merchant_trace ==")
t = lineage.merchant_trace(mid, db_path=db)
check("trace ok", t["ok"] is True, str(t))
check("resolved via source_file_id ('id' link)", t["link"] == "id", t["link"])
check("source file attached with hash + display name",
      t["source_file"] and t["source_file"]["hash8"] == "abcd1234"
      and t["source_file"]["file_name"] == "My Workbook.xlsx", str(t["source_file"]))
check("merchant block carries sheet + physical row",
      t["merchant"]["row_number"] == 7 and t["merchant"]["sheet_name"]
      == "My Workbook :: Sheet1", str(t["merchant"]))

t2 = lineage.merchant_trace(unmatched, db_path=db)
check("untraceable row -> link 'none', no source_file",
      t2["ok"] and t2["link"] == "none" and t2["source_file"] is None,
      str(t2["link"]))

# Fallback: stamped-by-sheet_name-only (no source_file_id)
mid_fb = add_merchant(db, "My Workbook :: Sheet1", 9, "FALLBACK LTD", sf_id=None)
conn = sqlite3.connect(str(db))
conn.execute("UPDATE merchants SET source_file_id = NULL WHERE id = ?",
             (mid_fb,))
conn.commit()
conn.close()
t3 = lineage.merchant_trace(mid_fb, db_path=db)
check("sheet_name fallback resolves when id is missing",
      t3["ok"] and t3["link"] == "sheet_name"
      and t3["source_file"] is not None, str(t3["link"]))

missing = lineage.merchant_trace(99999, db_path=db)
check("unknown merchant id reported", missing["ok"] is False)

# ── 3. file_summary ──────────────────────────────────────────────────────
print("== 3. file_summary ==")
s = lineage.file_summary(db_path=db)
check("summary ok with one file", s["ok"] and len(s["files"]) == 1, str(s))
f = s["files"][0]
check("file row counts merchants + hash + sheet breakdown",
      f["merchants"] >= 2 and f["hash8"] == "abcd1234"
      and f["merchant_sheets"][0]["rows"] >= 2, str(f))
check("traced/total reported",
      s["traced_merchants"] >= 2 and s["total_merchants"] >= 3, str(s))

# ── 4. file_trace ────────────────────────────────────────────────────────
print("== 4. file_trace ==")
ft = lineage.file_trace("workbook", db_path=db)
check("matches by file-name fragment",
      ft["ok"] and len(ft["files"]) == 1
      and ft["files"][0]["file_name"] == "My Workbook.xlsx", str(ft))
check("sample merchants attached",
      len(ft["files"][0]["sample_merchants"]) >= 1)
ft2 = lineage.file_trace("no-such-file", db_path=db)
check("no-match reported cleanly", ft2["ok"] is False)
ft3 = lineage.file_trace("", db_path=db)
check("empty query rejected", ft3["ok"] is False)

# ── 5. missing DB ────────────────────────────────────────────────────────
print("== 5. missing DB ==")
check("helpers report missing DB",
      lineage.file_summary(db_path=TMP / "nope.db")["ok"] is False
      and lineage.merchant_trace(1, db_path=TMP / "nope.db")["ok"] is False)

print()
print(f"RESULT: {checks - fails}/{checks} checks passed")
sys.exit(1 if fails else 0)
