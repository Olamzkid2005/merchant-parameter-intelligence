"""Hermetic checks for merchant_intelligence/ingest_ledger.py — the
append-only rebuild ledger + freshness signal (governed data platform slice).

All state is pointed at temp files/folders via env + kwargs so the real
data/ingest_ledger.db and data/ folder are never touched.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["INGEST_LEDGER_FILE"] = ""

passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def make_ledger(tmp):
    """Fresh ledger db in a temp dir; returns (path, module)."""
    from merchant_intelligence import ingest_ledger as m
    p = tmp / "ledger.db"
    m._LEDGER_FILE = str(p)
    return p, m


def make_sources(tmp):
    """A temp folder with two Excel-like files (ledger only cares about stat)."""
    d = tmp / "sources"
    d.mkdir(exist_ok=True)
    a = d / "alpha.xlsx"
    b = d / "beta.xlsx"
    a.write_bytes(b"abc")
    b.write_bytes(b"def")
    return d, a, b


def snapshot_of(m, d):
    """Record's sources arg shape: {Path: (mtime_ns, size)}."""
    out = {}
    for f in d.iterdir():
        if f.suffix == ".xlsx":
            st = f.stat()
            out[f] = (st.st_mtime_ns, st.st_size)
    return out


print("[1] record + recent + stats")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _, m = make_ledger(tmp)

    rid = m.record("build_intelligence_db", "ok", detail="alpha verified", row_count=42)
    check("record returns run id 1", rid == 1, repr(rid))
    rid2 = m.record("rebuild_databases", "failed", detail="beta failed")
    check("second record returns id 2", rid2 == 2, repr(rid2))

    r = m.recent()
    check("recent newest-first", len(r) == 2 and r[0]["id"] == 2 and r[1]["id"] == 1)
    check("status stored", r[0]["status"] == "failed" and r[1]["status"] == "ok")
    check("row_count stored", r[1]["row_count"] == 42)

    s = m.stats()
    check("stats totals", s["runs"] == 2 and s["ok"] == 1 and s["failed"] == 1, repr(s))

print("[2] append-only invariant — no UPDATE/DELETE in module source")
with tempfile.TemporaryDirectory() as td:
    src = (ROOT / "merchant_intelligence" / "ingest_ledger.py").read_text(encoding="utf-8")
    for banned in ("UPDATE ", "DELETE FROM", "DROP TABLE"):
        check(f"no {banned.strip()!r} anywhere", banned not in src)

print("[3] freshness — clean vs stale")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _, m = make_ledger(tmp)
    d, a, b = make_sources(tmp)
    snap = snapshot_of(m, d)

    # No runs yet -> last_ok_run None, everything stale
    f = m.freshness(folder=d)
    check("no runs -> not fresh", f["fresh"] is False)
    check("no runs -> both sources 'new'", len(f["stale_sources"]) == 2
          and all(x["status"] == "new" for x in f["stale_sources"]), repr(f["stale_sources"]))
    check("no runs -> last_ok_run None", f["last_ok_run"] is None)
    check("source_count", f["source_count"] == 2, repr(f))

    # Record a build with the exact snapshot -> fresh
    m.record("build_intelligence_db", "ok", row_count=7, sources=snap)
    f = m.freshness(folder=d)
    check("after matching build -> fresh", f["fresh"] is True, repr(f["stale_sources"]))
    check("last_ok_run present", f["last_ok_run"] is not None
          and f["last_ok_run"]["row_count"] == 7)

    # Touch beta -> changed
    time.sleep(0.02)
    b.write_bytes(b"defgh")
    f = m.freshness(folder=d)
    check("touched file -> stale", f["fresh"] is False)
    beta = [x for x in f["stale_sources"] if x["name"].endswith("beta.xlsx")]
    check("beta flagged 'changed'", len(beta) == 1 and beta[0]["status"] == "changed",
          repr(f["stale_sources"]))
    check("alpha not flagged", not any(x["name"].endswith("alpha.xlsx")
                                       for x in f["stale_sources"]))

    # New file -> 'new'
    c = d / "gamma.xlsx"
    c.write_bytes(b"ghi")
    f = m.freshness(folder=d)
    gamma = [x for x in f["stale_sources"] if x["name"].endswith("gamma.xlsx")]
    check("new file flagged 'new'", len(gamma) == 1 and gamma[0]["status"] == "new")

print("[4] fresh build resets the signal")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    _, m = make_ledger(tmp)
    d, a, b = make_sources(tmp)
    snap = snapshot_of(m, d)
    m.record("rebuild_databases", "ok", row_count=10, sources=snap)
    f = m.freshness(folder=d)
    check("fresh after snapshot build", f["fresh"] is True)
    check("db_rows is int (0 when no intelligence.db)", isinstance(f["db_rows"], int))

print("[5] record never raises on bad input")
with tempfile.TemporaryDirectory() as td:
    _, m = make_ledger(Path(td))
    check("bad sources -> None, no raise", m.record("x", "ok", sources={123: "junk"}) is None)
    check("bad row_count -> None, no raise", m.record("x", "ok", row_count="nope") is None)
    # Bad args abort BEFORE the insert (coercion happens first), so the next
    # good record still lands with a valid id.
    rid = m.record("x", "ok", row_count=3)
    check("good record after failures still works", rid is not None and rid >= 1, repr(rid))

print("[6] db file physically created with schema")
with tempfile.TemporaryDirectory() as td:
    p, m = make_ledger(Path(td))
    m.record("build_intelligence_db", "ok")
    conn = sqlite3.connect(str(p))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    check("runs table exists", "runs" in tables, repr(tables))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
