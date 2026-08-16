"""
audit.py — Immutable, append-only audit trail (docs/technical-review-2026-08-original.md #1).

Roadmap item #1 (Enterprise Security & Compliance) first slice: every search,
profile view, export, and intent execution is recorded with actor, timestamp,
action, and scope — the capture layer the self-improvement flywheel (#5) also
depends on.

Design decisions:
- Dedicated SQLite file (data/audit_log.db), NOT the merchant DB: the
  intelligence DB is rebuilt from scratch by the build scripts, which would
  wipe an audit table living there. This file survives rebuilds.
- Append-only by construction: `record()` is the ONLY write path. No
  update/delete API exists, and rows carry an AUTOINCREMENT id so ordering
  and append-only-ness are checkable.
- Best-effort: callers wrap in try/except — an audit failure must never
  break a user request (the caller-facing contract is "log or swallow").
- The actor defaults to "local" (single-user desktop tool). When authN/Z
  (#1's second slice) lands, the request-scoped actor replaces it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    actor   TEXT    NOT NULL DEFAULT 'local',
    action  TEXT    NOT NULL,
    scope   TEXT,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""

_ACTIONS = {
    "search", "profile", "task", "task_analyze", "export",
    "brief", "reconcile", "batch", "quickmatch", "learn",
}

_lock = threading.Lock()
_conns: Dict[str, sqlite3.Connection] = {}


def _path() -> Any:
    override = os.environ.get("MERCHANT_AUDIT_DB")
    if override:
        from pathlib import Path
        return Path(override)
    return config.DATA_DIR / "audit_log.db"


def _connect() -> sqlite3.Connection:
    path = str(_path())
    with _lock:
        conn = _conns.get(path)
        if conn is None:
            _path().parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            _conns[path] = conn
        return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def record(action: str, scope: Optional[str] = None,
           detail: Optional[str] = None, actor: str = "local") -> None:
    """Append one audit entry (the ONLY write path — append-only by design).

    action: one of _ACTIONS (unknown actions are still recorded; the set is
            for UI filters, not a hard gate).
    scope:  compact JSON string summarising the request (query, ids, etc.)
    detail: free-text note.
    """
    try:
        conn = _connect()
        with _lock:
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, scope, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), (actor or "local")[:64], (action or "?")[:64],
                 (scope or "")[:2000], (detail or "")[:2000]))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — audit must never break a request
        logger.warning("audit record failed (%s): %s", action, exc)


def recent(limit: int = 200, action: Optional[str] = None,
           actor: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest-first audit entries, optionally filtered by action/actor."""
    try:
        conn = _connect()
        sql = "SELECT id, ts, actor, action, scope, detail FROM audit_log"
        where, params = [], []
        if action:
            where.append("action = ?")
            params.append(action)
        if actor:
            where.append("actor = ?")
            params.append(actor)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(1000, int(limit))))
        with _lock:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit read failed: %s", exc)
        return []


def stats() -> Dict[str, Any]:
    """Aggregate counts: per-action totals + last-24h, plus the newest ts."""
    try:
        conn = _connect()
        with _lock:
            total = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
            by_action = {
                r["action"]: r["n"] for r in conn.execute(
                    "SELECT action, COUNT(*) AS n FROM audit_log "
                    "GROUP BY action ORDER BY n DESC")}
            last_24h = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_log "
                "WHERE ts >= datetime('now', '-1 day')").fetchone()["n"]
            newest = conn.execute(
                "SELECT MAX(ts) AS t FROM audit_log").fetchone()["t"]
        return {"total": total, "last_24h": last_24h,
                "by_action": by_action, "newest": newest}
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit stats failed: %s", exc)
        return {"total": 0, "last_24h": 0, "by_action": {}, "newest": None}
