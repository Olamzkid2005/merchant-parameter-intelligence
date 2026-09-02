"""
test_contact_fallback.py — cross-identifier contact fallback
(merchant_intelligence/tasks/db.py family_rows_for / cross_identifier_field,
wired into tasks/pipelines.py _pipeline_field).

Fully hermetic: temp SQLite DB, no real registry, no network.

Covers the BIDWILL scenario that motivated the feature: a merchant keyed by
MX183515 on the static-account sheet (no email column there) whose email
lives on a sibling NNPC-sheet row keyed by MX184740 — plus the shared-
identifier guard (one MX registered to two merchants must never leak a
contact across them).
"""
import os
import sys
import tempfile

import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merchant_intelligence.tasks.db import (  # noqa: E402
    cross_identifier_field, family_rows_for,
)
from merchant_intelligence.tasks.pipelines import _pipeline_field  # noqa: E402

_PASSED = _FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f"  ok  {name}")
    else:
        _FAILED += 1
        print(f"  FAIL {name}  {detail}")


SCHEMA = """
CREATE TABLE merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_name TEXT, row_number INTEGER, merchant_name TEXT,
    merchant_id TEXT, mxcode TEXT, payable_code TEXT, tid TEXT,
    terminal_serial TEXT, slip_header TEXT, email TEXT, phone TEXT,
    address TEXT, contact_name TEXT, contact_title TEXT, account_name TEXT,
    account_number TEXT, bank TEXT, state TEXT, state_code TEXT,
    bvn TEXT, ptsp TEXT, terminal_type TEXT, deployment_status TEXT,
    alias TEXT, static_acc_no TEXT, remarks TEXT, raw_data TEXT,
    onboarded_date TEXT, imported_at TEXT
);
"""

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
conn = sqlite3.connect(tmp.name)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)

# BIDWILL-style family: 4 static-account rows keyed MX1 (no email) +
# 4 NNPC rows keyed MX2 with the shared TIDs and the email.
FAMILY = [
    # id, name,                  tid,        mx,   email,              sheet
    (1, "BIDWILL ENERGY RESOURCES LTD - NNPC", "2103O084", "MX1", "", "static_account_terminal 24 aug2026 :: static_account_terminal"),
    (2, "BIDWILL ENERGY RESOURCES LTD - NNPC", "2103O085", "MX1", "", "static_account_terminal 24 aug2026 :: static_account_terminal"),
    (3, "BIDWILL ENERGY RESOURCES LTD",        "2103O084", "MX2", "willy@yahoo.com", "NNPC Updated 17aug :: Sheet1"),
    (4, "BIDWILL ENERGY RESOURCES LTD",        "2103O085", "MX2", "willy@yahoo.com", "NNPC Updated 17aug :: Sheet1"),
    # Shared-MX decoys: MX9 belongs to two UNRELATED merchants; only row 7
    # carries an email — it must never reach row 8's family harvest.
    (7, "ALPHA MERCHANT",  "T777", "MX9", "alpha@x.com", "Sheet A"),
    (8, "BETA MERCHANT",   "T888", "MX9", "",            "Sheet B"),
]
for rid, name, tid, mx, email, sheet in FAMILY:
    conn.execute(
        "INSERT INTO merchants (id, sheet_name, row_number, merchant_name,"
        " mxcode, tid, email, phone, static_acc_no)"
        " VALUES (?,?,?,?,?,?,?,?,'')",
        (rid, sheet, rid + 1, name, mx, tid, email, "08000000000"))
conn.commit()

bidwill_sa = dict(conn.execute(
    "SELECT * FROM merchants WHERE id=1").fetchone())
bidwill_nnpc = dict(conn.execute(
    "SELECT * FROM merchants WHERE id=3").fetchone())
alpha = dict(conn.execute("SELECT * FROM merchants WHERE id=7").fetchone())
beta = dict(conn.execute("SELECT * FROM merchants WHERE id=8").fetchone())

# ── 1. Family discovery ──────────────────────────────────────────────────
print("\n[1] family_rows_for links sibling rows via exact TID")
fam = family_rows_for(conn, bidwill_sa)
fam_ids = {s["id"] for s in fam}
# One hop, not transitive: row 1's TID (2103O084) links row 3 directly;
# row 4 is reached from row 1 only via row 2, so it is NOT in row 1's
# family — every SA terminal still reaches its own NNPC sibling.
check("static-account row finds NNPC siblings (TID link)",
      fam_ids == {2, 3}, fam_ids)
fam2 = family_rows_for(conn, bidwill_nnpc)
fam2_ids = {s["id"] for s in fam2}
check("NNPC row finds static-account siblings",
      fam2_ids == {1, 4}, fam2_ids)

# ── 2. Contact harvest ───────────────────────────────────────────────────
print("\n[2] cross_identifier_field harvests the family email")
val, srcs = cross_identifier_field(conn, bidwill_sa, "email")
check("email harvested from sibling sheet",
      val == "willy@yahoo.com", repr(val))
check("provenance points at the NNPC sheet + TID",
      len(srcs) == 1 and srcs[0]["tid"] == "2103O084"
      and "NNPC" in (srcs[0]["sheet_name"] or ""),
      [(s["sheet_name"], s["tid"]) for s in srcs])
val2, _ = cross_identifier_field(conn, bidwill_nnpc, "email")
check("row that already has the email returns no fallback", val2 == "")

# ── 3. Financial fields are never family-harvested ───────────────────────
print("\n[3] financial fields excluded")
val3, _ = cross_identifier_field(conn, bidwill_sa, "static_acc_no")
check("static account never pulled from a sibling", val3 == "")
val4, _ = cross_identifier_field(conn, bidwill_sa, "payable_code")
check("payable never pulled from a sibling", val4 == "")

# ── 4. Shared-identifier guard ───────────────────────────────────────────
print("\n[4] shared MX across distinct merchants never leaks")
fam_a = family_rows_for(conn, alpha)
check("ALPHA family excludes BETA",
      all(s["merchant_name"] != "BETA MERCHANT" for s in fam_a),
      [(s["merchant_name"]) for s in fam_a])
fam_b = family_rows_for(conn, beta)
check("BETA family excludes ALPHA",
      all(s["merchant_name"] != "ALPHA MERCHANT" for s in fam_b),
      [(s["merchant_name"]) for s in fam_b])
val_b, _ = cross_identifier_field(conn, beta, "email")
check("BETA harvests no email through the shared MX", val_b == "", repr(val_b))

# ── 5. Pipeline integration ──────────────────────────────────────────────
print("\n[5] _pipeline_field marks the row found_via_family with Via data")
task = {"identifiers": {"mxcode": ["MX1"]}, "names": [], "named": []}
res = _pipeline_field(conn, task, "email", "Email", "email")
# Field requests dedupe by field VALUE: both MX1 terminals harvest the
# same family email, so they collapse to one row (the standard
# 'a value in several sheets never repeats' rule).
check("MX1 rows deduped to one email row", len(res["rows"]) == 1,
      len(res["rows"]))
check("Via column present in output",
      "Via" in res["columns"], res["columns"])
for r in res["rows"]:
    check(f"row {r['identifier']}: email via family",
          r["Email"] == "willy@yahoo.com"
          and r["status"] == "found_via_family"
          and "NNPC" in r.get("via", ""),
          r)

# Ordinary case keeps the standard columns (no Via). Status is no_name
# (no pasted name to compare) which the UI renders as 'Found'.
task2 = {"identifiers": {"mxcode": ["MX2"]}, "names": [], "named": []}
res2 = _pipeline_field(conn, task2, "email", "Email", "email")
check("direct-hit rows keep the plain column set and direct status",
      "Via" not in res2["columns"]
      and all(r["status"] in ("found", "no_name") for r in res2["rows"]),
      (res2["columns"], [(r["status"]) for r in res2["rows"]]))

conn.close()
os.unlink(tmp.name)

print(f"\nRESULT: {_PASSED} passed, {_FAILED} failed")
sys.exit(1 if _FAILED else 0)
