"""migrations.py — schema versioning + ordered migrations for the three
merchant databases (roadmap #2: governed data platform).

Why
    Every rebuild DELETES and recreates the databases, so any table added
    outside the build scripts (the normalized platform tables, and all
    future schema work) is silently wiped and never comes back. schema.py's
    migration exists but is manual-only and unversioned — after a rebuild
    the DB is back to the bare build-script schema and nothing re-applies
    it (``source_files`` is missing, so ``ingestion.detect_changes`` /
    ``POST /api/ingestion/scan`` fail with "no such table").

How
    - Version tracking: SQLite's native ``PRAGMA user_version`` (stored in
      the DB file header — no extra table, survives VACUUM, trivially
      readable).
    - Ordered, idempotent migration list per concern. ``apply_migrations``
      applies every migration with version > the DB's current version
      inside a transaction, then stamps the target version. Re-running is
      always safe; a DB NEWER than the code is reported, never downgraded.
    - Re-applied automatically after every rebuild (app.start pipeline +
      watcher REBUILD_STEPS) and best-effort at API startup, so the schema
      converges no matter which path built the DB.

Scope decision
    Only DATA-PLATFORM tables migrate automatically. The auth/tenancy and
    encryption tables (app_users/app_roles/user_roles/encryption_keys) stay
    behind the explicit ``POST /api/schema/migrate`` endpoint — auto-seeding
    a default admin user on every startup would be a security smell, and
    auth on this desktop tool is opt-in (see docs #1).

Run:  python -m merchant_intelligence.migrations   (apply to all three DBs)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The three databases, in rebuild order. All receive the same migration list
# (every statement is CREATE ... IF NOT EXISTS, so applying to a DB that
# never uses a table is harmless and keeps the trio consistent).
DB_PATHS = [
    _PROJECT_ROOT / "data" / "merchant_search.db",
    _PROJECT_ROOT / "data" / "intelligence.db",
    _PROJECT_ROOT / "data" / "merchant_intel.db",
]

# ── Migration registry ──────────────────────────────────────────────────────
# (version, description, sql). Versions must be consecutive starting at 1;
# append-only — never edit an applied migration, add a new one.

_MIGRATION_V1_BASELINE = """
-- v1: baseline marker. The merchants/FTS/aliases schema is created by the
-- build scripts; this migration only stamps that the DB is governed.
SELECT 1;
"""

_MIGRATION_V2_DATA_PLATFORM = """
-- v2: normalized data-platform tables (mirrors schema.py's DDL for these
-- four tables; auth/encryption tables intentionally excluded — see module
-- docstring). ingestion.py's CDC reads source_files; drift.py writes
-- data_quality_log.
CREATE TABLE IF NOT EXISTS source_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT NOT NULL,
    file_hash       TEXT,           -- SHA-256 of the file contents
    sheet_name      TEXT,
    row_count       INTEGER DEFAULT 0,
    column_names    TEXT,           -- JSON array of column headers
    ingested_at     TEXT NOT NULL,  -- ISO-8601
    status          TEXT DEFAULT 'ok',  -- ok | error | skipped
    error_message   TEXT,
    UNIQUE(file_path, sheet_name)
);

CREATE TABLE IF NOT EXISTS identifiers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id     INTEGER NOT NULL REFERENCES merchants(id),
    id_type         TEXT NOT NULL,  -- tid | mxcode | mid | bvn | phone | email | ...
    id_value        TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL,
    UNIQUE(merchant_id, id_type, id_value)
);
CREATE INDEX IF NOT EXISTS idx_identifiers_type_value
    ON identifiers(id_type, id_value);
CREATE INDEX IF NOT EXISTS idx_identifiers_merchant
    ON identifiers(merchant_id);

CREATE TABLE IF NOT EXISTS entity_clusters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      TEXT NOT NULL,   -- group key (e.g. cluster_<root>)
    merchant_id     INTEGER NOT NULL REFERENCES merchants(id),
    link_reason     TEXT NOT NULL,   -- shared_tid | shared_phone | ...
    link_strength   REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    UNIQUE(cluster_id, merchant_id, link_reason)
);
CREATE INDEX IF NOT EXISTS idx_clusters_id
    ON entity_clusters(cluster_id);
CREATE INDEX IF NOT EXISTS idx_clusters_merchant
    ON entity_clusters(merchant_id);

CREATE TABLE IF NOT EXISTS data_quality_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type       TEXT NOT NULL,   -- routing | recall | freshness | full
    status          TEXT NOT NULL,   -- ok | warning | critical
    details         TEXT,           -- JSON
    ts              TEXT NOT NULL    -- ISO-8601
);
"""

MIGRATIONS: List[Tuple[int, str, str]] = [
    (1, "baseline (build-script schema is governed)", _MIGRATION_V1_BASELINE),
    (2, "data platform tables: source_files, identifiers, "
        "entity_clusters, data_quality_log", _MIGRATION_V2_DATA_PLATFORM),
]

LATEST_VERSION = MIGRATIONS[-1][0]


# ── Runner ──────────────────────────────────────────────────────────────────

def get_version(conn: sqlite3.Connection) -> int:
    """Current schema version of an open database."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(db_path: Path,
                     migrations: Optional[List[Tuple[int, str, str]]] = None,
                     ) -> Dict[str, Any]:
    """Bring one database up to LATEST_VERSION. Idempotent.

    Returns {db, from_version, to_version, applied: [versions], ok,
    skipped_reason?}. A missing DB file is reported (the build scripts
    create it; we never create a registry DB out of thin air). A DB newer
    than the code is left untouched and reported — never downgraded.
    """
    migrations = migrations if migrations is not None else MIGRATIONS
    path = Path(db_path)
    if not path.exists():
        return {"ok": False, "db": str(path),
                "skipped_reason": "database file does not exist"}

    conn = sqlite3.connect(str(path), timeout=30)
    applied: List[int] = []
    try:
        current = get_version(conn)
        latest = migrations[-1][0]
        if current > latest:
            return {"ok": True, "db": str(path), "from_version": current,
                    "to_version": current, "applied": [],
                    "skipped_reason": f"database newer than code "
                                      f"(v{current} > v{latest})"}

        for version, description, sql in migrations:
            if version <= current:
                continue
            # executescript auto-commits any pending transaction, so the DDL
            # runs in autocommit — per-statement atomicity is all we get.
            # That's fine: every statement is CREATE ... IF NOT EXISTS, so a
            # partially-applied migration retries harmlessly, and the version
            # stamp is written only after the whole script succeeded.
            try:
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(version)

        return {"ok": True, "db": str(path),
                "from_version": current,
                "to_version": get_version(conn),
                "applied": applied}
    except Exception as exc:  # noqa: BLE001 — caller decides severity
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "db": str(path), "error": str(exc),
                "applied": applied}
    finally:
        conn.close()


def apply_all(paths: Optional[List[Path]] = None,
              migrations: Optional[List[Tuple[int, str, str]]] = None,
              ) -> Dict[str, Any]:
    """Apply migrations to every database. Never raises — startup and the
    rebuild pipeline call this best-effort; failures are reported per-DB.

    A MISSING database file is a normal state (before the first rebuild)
    and does not fail the aggregate — only a real migration error does."""
    out: List[Dict[str, Any]] = []
    ok = True
    for p in (paths if paths is not None else DB_PATHS):
        r = apply_migrations(p, migrations)
        out.append(r)
        ok = ok and "error" not in r
    return {"ok": ok, "results": out}


def versions(paths: Optional[List[Path]] = None) -> Dict[str, Any]:
    """Read each DB's schema version (for the status endpoint)."""
    out: Dict[str, Any] = {}
    for p in (paths if paths is not None else DB_PATHS):
        name = Path(p).name
        if not Path(p).exists():
            out[name] = None
            continue
        try:
            conn = sqlite3.connect(str(p), timeout=10)
            try:
                out[name] = get_version(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


def _as_script(migrations: List[Tuple[int, str, str]]) -> None:
    """CLI entry: apply to all three DBs and print a summary."""
    print("=" * 60)
    print(f"  SCHEMA MIGRATIONS — latest v{LATEST_VERSION}")
    print("=" * 60)
    result = apply_all(migrations=migrations)
    for r in result["results"]:
        if r.get("ok"):
            if r.get("skipped_reason"):
                print(f"  [..] {Path(r['db']).name}: {r['skipped_reason']}")
            elif r["applied"]:
                print(f"  [OK] {Path(r['db']).name}: "
                      f"v{r['from_version']} -> v{r['to_version']} "
                      f"(applied {r['applied']})")
            else:
                print(f"  [OK] {Path(r['db']).name}: already at "
                      f"v{r['to_version']}")
        else:
            print(f"  [X]  {Path(r['db']).name}: {r.get('error')}")
    print("=" * 60)


if __name__ == "__main__":
    _as_script(MIGRATIONS)
