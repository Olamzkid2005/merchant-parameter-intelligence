"""schema.py — Normalized schema migration + tenancy + encryption for intelligence.db.

Adds the following tables on top of the existing denormalized ``merchants``
table (which stays for backward compatibility):

  **Normalized data platform (roadmap #2):**
  - ``source_files``   — one row per ingested workbook (file path, hash,
                         ingested_at, row_count, status)
  - ``identifiers``    — one row per merchant×identifier-type (TID, MX,
                         MID, BVN, phone, email, …) with type+value+merchant_id
  - ``entity_clusters`` — groups of merchants sharing at least one identifier
                         (cluster_id, merchant_id, link_reason)

  **Tenancy-ready auth (roadmap #1):**
  - ``app_users``      — DB-backed user accounts (replaces JSON config)
  - ``app_roles``      — role definitions (viewer, analyst, administrator)
  - ``user_roles``     — many-to-many user↔role

  **Encryption at rest (roadmap #1):**
  - ``encryption_keys`` — master key metadata (version, created_at, active)

All tables use ``CREATE TABLE IF NOT IS`` so the migration is idempotent.
Existing code continues to read from ``merchants`` — the new tables are
additive and do not break any existing queries.

Run:  python -m merchant_intelligence.schema  (standalone migration)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


def _db_path() -> Path:
    override = os.environ.get("MERCHANT_INTELLIGENCE_DB")
    return Path(override) if override else _DATA_DIR / "intelligence.db"


# ── Schema DDL ──────────────────────────────────────────────────────────────

_DDL = """
-- ── source_files: one row per ingested workbook ──────────────────────────
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

-- ── identifiers: one row per merchant × identifier type ──────────────────
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

-- ── entity_clusters: groups sharing at least one identifier ──────────────
CREATE TABLE IF NOT EXISTS entity_clusters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      TEXT NOT NULL,   -- UUID or human-readable group key
    merchant_id     INTEGER NOT NULL REFERENCES merchants(id),
    link_reason     TEXT NOT NULL,   -- shared_tid | shared_phone | shared_bvn | ...
    link_strength   REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    UNIQUE(cluster_id, merchant_id, link_reason)
);
CREATE INDEX IF NOT EXISTS idx_clusters_id
    ON entity_clusters(cluster_id);
CREATE INDEX IF NOT EXISTS idx_clusters_merchant
    ON entity_clusters(merchant_id);

-- ── app_users: DB-backed user accounts (replaces JSON config) ───────────
CREATE TABLE IF NOT EXISTS app_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    password_hash   TEXT NOT NULL,
    salt            TEXT NOT NULL,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL,
    last_login      TEXT
);

-- ── app_roles: role definitions ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name       TEXT NOT NULL UNIQUE,
    description     TEXT,
    permissions     TEXT  -- JSON array of permission strings
);

-- ── user_roles: many-to-many ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_roles (
    user_id         INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    role_id         INTEGER NOT NULL REFERENCES app_roles(id) ON DELETE CASCADE,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

-- ── encryption_keys: master key metadata ────────────────────────────────
CREATE TABLE IF NOT EXISTS encryption_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key_version     INTEGER NOT NULL UNIQUE,
    key_hash        TEXT NOT NULL,   -- SHA-256 of the key (never store raw)
    algorithm       TEXT DEFAULT 'aes-256-gcm',
    created_at      TEXT NOT NULL,
    active          INTEGER DEFAULT 1
);

-- ── data_quality_log: quality scan history ───────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type       TEXT NOT NULL,   -- routing | recall | freshness | full
    status          TEXT NOT NULL,   -- ok | warning | critical
    details         TEXT,           -- JSON
    ts              TEXT NOT NULL    -- ISO-8601
);
"""


# ── Migration runner ────────────────────────────────────────────────────────

def migrate(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Run the schema migration. Idempotent — safe to call multiple times."""
    path = db_path or _db_path()
    if not path.exists():
        return {"ok": False, "error": f"Database not found: {path}"}

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_DDL)

        # Seed default roles if empty
        count = conn.execute("SELECT COUNT(*) FROM app_roles").fetchone()[0]
        if count == 0:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            default_roles = [
                ("viewer", "Read-only access with field masking",
                 json.dumps(["search", "profile", "export"])),
                ("analyst", "Full search + export, no admin settings",
                 json.dumps(["search", "profile", "export", "task", "copilot"])),
                ("administrator", "Full access including settings and audit",
                 json.dumps(["search", "profile", "export", "task", "copilot",
                             "admin", "audit", "user_management"])),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO app_roles (role_name, description, permissions) "
                "VALUES (?, ?, ?)", default_roles)

        # Seed default admin user if empty
        user_count = conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]
        if user_count == 0:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            salt = secrets.token_hex(16)
            pw_hash = hashlib.pbkdf2_hmac(
                "sha256", "admin".encode(), salt.encode(), 100_000).hex()
            conn.execute(
                "INSERT INTO app_users (username, display_name, password_hash, "
                "salt, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                ("admin", "Administrator", pw_hash, salt, now))
            admin_id = conn.execute(
                "SELECT id FROM app_users WHERE username='admin'").fetchone()[0]
            admin_role = conn.execute(
                "SELECT id FROM app_roles WHERE role_name='administrator'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO user_roles (user_id, role_id, assigned_at) "
                "VALUES (?, ?, ?)", (admin_id, admin_role, now))

        # Initialize encryption key metadata if empty
        key_count = conn.execute("SELECT COUNT(*) FROM encryption_keys").fetchone()[0]
        if key_count == 0:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            # Generate a master key and store its hash
            master_key = secrets.token_bytes(32)
            key_hash = hashlib.sha256(master_key).hexdigest()
            conn.execute(
                "INSERT INTO encryption_keys (key_version, key_hash, created_at, active) "
                "VALUES (1, ?, ?, 1)", (key_hash, now))

        conn.commit()

        # Count new tables
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        return {
            "ok": True,
            "db": str(path),
            "tables": sorted(tables),
            "new_tables": ["source_files", "identifiers", "entity_clusters",
                           "app_users", "app_roles", "user_roles",
                           "encryption_keys", "data_quality_log"],
        }
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


# ── Identifier extraction (from merchants table → identifiers table) ────────

def populate_identifiers(db_path: Optional[Path] = None,
                         batch_size: int = 5000) -> Dict[str, Any]:
    """Extract identifiers from the denormalized merchants table into the
    normalized identifiers table.  Idempotent (INSERT OR IGNORE).

    This is the bridge between the legacy schema and the new normalized
    platform — it runs once after migration and again on each rebuild.
    """
    path = db_path or _db_path()
    conn = sqlite3.connect(str(path))
    try:
        # Check if identifiers already populated
        count = conn.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0]
        if count > 0:
            return {"ok": True, "skipped": True,
                    "reason": f"Already populated ({count} rows)"}

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Map of DB column → id_type
        ID_MAP = [
            ("tid", "tid"),
            ("mxcode", "mxcode"),
            ("merchant_id", "mid"),
            ("bvn", "bvn"),
            ("phone", "phone"),
            ("email", "email"),
            ("static_acc_no", "static_acc"),
            ("account_number", "account_number"),
            ("payable_code", "payable"),
            ("alias", "alias"),
        ]

        rows = conn.execute(
            "SELECT id, " + ", ".join(col for col, _ in ID_MAP) +
            " FROM merchants"
        ).fetchall()

        inserts = []
        for row in rows:
            merchant_db_id = row[0]
            for i, (col, id_type) in enumerate(ID_MAP):
                val = row[i + 1]
                if val and str(val).strip():
                    inserts.append((merchant_db_id, id_type,
                                    str(val).strip(), 1.0, now))

        conn.executemany(
            "INSERT OR IGNORE INTO identifiers "
            "(merchant_id, id_type, id_value, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?)", inserts)

        conn.commit()
        return {"ok": True, "inserted": len(inserts),
                "merchants": len(rows)}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


# ── Entity cluster detection ────────────────────────────────────────────────

def build_entity_clusters(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Find merchants sharing identifiers and group them into clusters.

    Two merchants are in the same cluster if they share any identifier
    (TID, phone, BVN, email, etc.).  This is the foundation for the
    Similar/Related merchants panel and the copilot's entity resolution.
    """
    path = db_path or _db_path()
    conn = sqlite3.connect(str(path))
    try:
        # Check if already populated
        count = conn.execute("SELECT COUNT(*) FROM entity_clusters").fetchone()[0]
        if count > 0:
            return {"ok": True, "skipped": True,
                    "reason": f"Already populated ({count} rows)"}

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Find merchants sharing identifiers via a self-join
        rows = conn.execute("""
            SELECT DISTINCT
                a.merchant_id AS merchant_a,
                b.merchant_id AS merchant_b,
                a.id_type,
                a.id_value,
                1.0 AS strength
            FROM identifiers a
            JOIN identifiers b
              ON a.id_type = b.id_type
              AND a.id_value = b.id_value
              AND a.merchant_id < b.merchant_id
        """).fetchall()

        # Union-Find to group into clusters
        parent: Dict[int, int] = {}

        def find(x: int) -> int:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for ma, mb, id_type, id_val, strength in rows:
            union(ma, mb)

        # Group merchants by cluster root
        clusters: Dict[int, List[int]] = {}
        all_merchants = set()
        for ma, mb, *_ in rows:
            all_merchants.add(ma)
            all_merchants.add(mb)
        for m in all_merchants:
            root = find(m)
            clusters.setdefault(root, []).append(m)

        # Insert clusters
        inserts = []
        for root, members in clusters.items():
            if len(members) < 2:
                continue  # Skip singletons
            cluster_id = f"cluster_{root}"
            for member in members:
                inserts.append((cluster_id, member, "shared_identifier",
                                1.0, now))

        if inserts:
            conn.executemany(
                "INSERT OR IGNORE INTO entity_clusters "
                "(cluster_id, merchant_id, link_reason, link_strength, created_at) "
                "VALUES (?, ?, ?, ?, ?)", inserts)

        conn.commit()
        n_clusters = len([m for m in clusters.values() if len(m) >= 2])
        return {"ok": True, "clusters": n_clusters,
                "links": len(rows),
                "clustered_merchants": len(all_merchants)}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


# ── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(_PROJECT_ROOT))

    print("=" * 60)
    print("  Schema Migration + Normalization")
    print("=" * 60)

    print("\n[1] Running schema migration...")
    result = migrate()
    print(f"    {result}")

    if result["ok"]:
        print("\n[2] Populating identifiers table...")
        id_result = populate_identifiers()
        print(f"    {id_result}")

        print("\n[3] Building entity clusters...")
        cluster_result = build_entity_clusters()
        print(f"    {cluster_result}")

    print("\n" + "=" * 60)
    print("  DONE")
    print("=" * 60)
