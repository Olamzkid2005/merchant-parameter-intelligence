"""ingest_ledger.py — governed-data-platform slice: an append-only ledger of
every ingestion run (rebuilds) plus a live data-freshness signal.

Why a separate database file?
    intelligence.db is wiped on every rebuild by design, so any ledger kept
    there would forget its own history. This module keeps an append-only
    SQLite db in data/ingest_ledger.db that survives rebuilds — the same
    pattern as audit_log.db.

What it records (per run):
    - run id / started / finished / status / duration
    - which database(s) were rebuilt and total rows ingested
    - the source snapshot (per-Excel file: name, size, mtime_ns) taken at
      build time, so freshness can be computed later

What freshness() answers:
    - was the last run successful and when?
    - which source files are NEW or CHANGED since the last recorded snapshot?
      (a file is changed when its mtime/size no longer match the snapshot)
    - how many rows are in intelligence.db right now?

The write path is deliberately tiny: record() is the only writer, entries are
insert-only (no UPDATE/DELETE anywhere), and failures never raise into the
caller (best-effort, like audit.py).
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Env override so tests can point at a temp file (same seam as audit/auth).
_LEDGER_FILE = os.environ.get("INGEST_LEDGER_FILE", "")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status      TEXT NOT NULL,          -- ok | failed
    pipeline    TEXT NOT NULL,          -- e.g. "rebuild_databases" | "build_intelligence_db"
    detail      TEXT NOT NULL DEFAULT '',   -- human-readable note
    row_count   INTEGER NOT NULL DEFAULT 0, -- rows in the target db after the run
    sources     TEXT NOT NULL DEFAULT '{}'  -- JSON {name: {size, mtime_ns}}
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
"""


def _db_path() -> Path:
    if _LEDGER_FILE:
        return Path(_LEDGER_FILE)
    return config.DATA_DIR / "ingest_ledger.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _norm_key(p: Path) -> str:
    """Canonical ledger key for a path.

    os.path.normcase makes the key case-insensitive on Windows (and is a
    no-op elsewhere) — critical because the same file is snapshotted through
    two different path spellings: scripts/build_intelligence_db.folder_snapshot()
    uses Path.resolve() (canonical on-disk casing, e.g. .../Downloads/...)
    while _folder_snapshot() below walks from config.DATA_DIR, which inherits
    whatever casing the app was LAUNCHED with (run.bat vs a lowercase shell
    cd). Without normcase every key mismatches and freshness reports all
    sources as permanently "new". Backslashes are normalized to forward
    slashes so keys are stable text across runs.
    """
    return os.path.normcase(str(p)).replace("\\", "/")


def _snapshot_of(snapshot: Dict[Path, Any]) -> str:
    """Serialize a {path: (mtime_ns, size)} snapshot for storage.

    Keys are normalized (see _norm_key) so the same file is comparable
    across runs regardless of launch-path casing.
    """
    out: Dict[str, Any] = {}
    for p, meta in snapshot.items():
        try:
            mtime, size = meta[0], meta[1]
        except (TypeError, IndexError):
            mtime, size = 0, 0
        out[_norm_key(p)] = {"mtime_ns": int(mtime), "size": int(size)}
    return json.dumps(out, sort_keys=True)


def _snapshot_of_file(path: Path) -> Dict[str, Any]:
    """Take the (mtime_ns, size) snapshot of a single Excel file."""
    try:
        st = path.stat()
        return {_norm_key(path): {"mtime_ns": st.st_mtime_ns, "size": st.st_size}}
    except OSError:
        return {}


def record(pipeline: str, status: str, detail: str = "",
           row_count: int = 0, sources: Optional[Dict[Path, Any]] = None,
           started_at: Optional[str] = None) -> Optional[int]:
    """Append one run entry. Returns the new run id, or None on failure.

    Never raises — ingestion must not break because the ledger is busy.
    """
    try:
        with _lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    """INSERT INTO runs
                       (started_at, finished_at, status, pipeline, detail,
                        row_count, sources)
                       VALUES (?,?,?,?,?,?,?)""",
                    (started_at or datetime.now().isoformat(timespec="seconds"),
                     datetime.now().isoformat(timespec="seconds"),
                     "ok" if status == "ok" else "failed",
                     pipeline, detail, int(row_count),
                     _snapshot_of(sources or {})),
                )
                conn.commit()
                run_id = cur.lastrowid
            finally:
                conn.close()
            return run_id
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        logger.warning("ingest_ledger.record failed: %s", e)
        return None


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    """Most recent runs, newest first."""
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("ingest_ledger.recent failed: %s", e)
        return []


def stats() -> Dict[str, Any]:
    """Totals across all recorded runs."""
    try:
        conn = _connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            ok = conn.execute("SELECT COUNT(*) FROM runs WHERE status='ok'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM runs WHERE status='failed'").fetchone()[0]
            last = conn.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0]
            rows = conn.execute("SELECT MAX(row_count) FROM runs").fetchone()[0] or 0
            return {"runs": int(total), "ok": int(ok), "failed": int(failed),
                    "last_run_at": last, "max_rows": int(rows)}
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("ingest_ledger.stats failed: %s", e)
        return {"runs": 0, "ok": 0, "failed": 0, "last_run_at": None, "max_rows": 0}


def _db_row_count() -> int:
    """Count rows currently in intelligence.db (best-effort)."""
    try:
        conn = sqlite3.connect(str(config.INTELLIGENCE_DB), timeout=10)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0])
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


# Derived/export files the app itself wrote into data/ ("Export to Excel"
# downloads). scripts/build_intelligence_db.py EXCLUDED_EXPORTS skips them
# at ingestion AND in its own snapshot — this scan must skip them too, or
# they read as permanently "new" and a watch-mode consumer would rebuild in
# an endless loop. Keep in sync with the script's set.
EXCLUDED_EXPORTS = {
    "medplus_tids.xlsx",
    "medplus_mids.xlsx",
}


def _folder_snapshot(folder: Path) -> Dict[Path, Any]:
    """Inline snapshot of every Excel file's (mtime_ns, size) in a folder.

    Mirrors scripts/build_intelligence_db.folder_snapshot() but lives here so
    the ledger has no dependency on a script module (build_intelligence_db
    imports rebuild_db bare, which only works as a script, not as an import).
    """
    snap: Dict[Path, Any] = {}
    if not folder.exists():
        return snap
    for ext in (".xlsx", ".xlsm", ".xls"):
        for p in folder.rglob(f"*{ext}"):
            if p.name.startswith("~$"):
                continue
            if p.name.lower() in EXCLUDED_EXPORTS:
                continue
            try:
                st = p.stat()
                snap[p] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return snap


def _norm_baseline(baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stored snapshot keys so old ledger entries (recorded before
    _norm_key existed, with launch-path-dependent casing) still compare."""
    return {k.replace("\\", "/").lower() if os.name == "nt"
            else k.replace("\\", "/"): v for k, v in baseline.items()}


def freshness(folder: Optional[Path] = None) -> Dict[str, Any]:
    """Compare the current Excel source folder against the last recorded
    successful build snapshot.

    Returns:
        {
          "last_ok_run": {...} | None,
          "db_rows": int,
          "stale_sources": [ {name, mtime_ns, size, status: "new"|"changed"} ],
          "fresh": bool,          # True when every source matches the last build
          "source_count": int,
        }
    """
    folder = folder or config.DATA_DIR

    last_ok: Optional[Dict[str, Any]] = None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            last_ok = dict(row)
            try:
                last_ok["sources"] = json.loads(row["sources"] or "{}")
            except ValueError:
                last_ok["sources"] = {}
    finally:
        conn.close()

    stale: List[Dict[str, Any]] = []
    if folder.exists():
        current = _folder_snapshot(folder)
        baseline = _norm_baseline((last_ok or {}).get("sources", {}))
        for path, meta in sorted(current.items()):
            key = _norm_key(path)
            rec = {"name": key, "mtime_ns": meta[0], "size": meta[1]}
            if key not in baseline:
                rec["status"] = "new"
            elif baseline[key] != {"mtime_ns": int(meta[0]), "size": int(meta[1])}:
                rec["status"] = "changed"
            else:
                continue
            stale.append(rec)

    return {
        "last_ok_run": last_ok,
        "db_rows": _db_row_count(),
        "stale_sources": stale,
        "fresh": not stale,
        "source_count": len(_folder_snapshot(folder)) if folder.exists() else 0,
    }
