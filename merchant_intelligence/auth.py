"""
auth.py — Opt-in authentication, RBAC, and field-level masking
(docs/technical-review-2026-08-original.md #1, second slice).

OPT-IN BY DEFAULT: `enabled` is false until someone turns it on (API or
config file). When disabled, every request passes through untouched and the
actor stays "local" — the desktop-tool experience is byte-for-byte the same.
When enabled, the FastAPI middleware in api.py enforces:

  - a session cookie (mi_session) on every route except /api/health and
    /api/auth/login;
  - role rules per route: viewer (masked reads) < analyst (full reads, no
    exports) < administrator (exports, settings, intent editing, audit,
    user management);
  - field-level masking of sensitive columns (bvn, account_number,
    static_acc_no, phone, email) at the API boundary for viewer sessions.

Secrets: pbkdf2_hmac (sha256, 200k iters, per-user salt) for passwords and a
server-generated random `secret` used nowhere else (reserved for future
signed cookies; sessions are opaque random tokens persisted in data/).

Test seams: MERCHANT_SECURITY_CONFIG / MERCHANT_SESSIONS_FILE overrides.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger(__name__)

# Roles, ordered by privilege.
ROLES = ("viewer", "analyst", "administrator")
_LEVEL = {r: i for i, r in enumerate(ROLES)}  # viewer=0, analyst=1, admin=2

# Sensitive columns masked at the API boundary for viewer sessions.
SENSITIVE_KEYS = {"bvn", "account_number", "static_acc_no", "phone", "email"}

# RLock (reentrant): session helpers hold the lock while calling
# _write_sessions(), which acquires it again — a plain Lock would deadlock.
_lock = threading.RLock()

# Request-scoped actor set by the middleware (contextvars propagate into the
# sync handlers FastAPI runs in its threadpool).
_current_actor: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mi_actor", default="local")


def current_actor() -> str:
    return _current_actor.get()


# ── Config ─────────────────────────────────────────────────────────────────
def _config_path() -> Path:
    override = os.environ.get("MERCHANT_SECURITY_CONFIG")
    return Path(override) if override else config.DATA_DIR / "security_config.json"


def _sessions_path() -> Path:
    override = os.environ.get("MERCHANT_SESSIONS_FILE")
    return Path(override) if override else config.DATA_DIR / "sessions.json"


def _default_config() -> Dict[str, Any]:
    return {"enabled": False, "secret": secrets.token_hex(32),
            "session_ttl_hours": 12, "users": []}


def load_config() -> Dict[str, Any]:
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _default_config()
            base.update({k: v for k, v in data.items()
                         if k in base and v is not None})
            base["users"] = [u for u in base.get("users", [])
                             if isinstance(u, dict) and u.get("username")
                             and u.get("role") in ROLES
                             and u.get("salt") and u.get("hash")]
            return base
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return _default_config()


def save_config(cfg: Dict[str, Any]) -> None:
    path = _config_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def enabled() -> bool:
    return bool(load_config().get("enabled"))


# ── Password hashing (pbkdf2, per-user salt) ───────────────────────────────
def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(digest.hex(), expected_hash)


# ── Sessions (opaque random tokens, persisted, expiring) ──────────────────
def _read_sessions() -> Dict[str, Dict[str, Any]]:
    path = _sessions_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    path = _sessions_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sessions, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def create_session(username: str, role: str) -> str:
    token = secrets.token_hex(32)
    ttl = float(load_config().get("session_ttl_hours", 12)) * 3600
    with _lock:
        sessions = _read_sessions()
        sessions[token] = {"username": username, "role": role,
                           "expires": time.time() + ttl}
        _write_sessions(sessions)
    return token


def get_session(token: str) -> Optional[Dict[str, str]]:
    if not token:
        return None
    with _lock:
        sessions = _read_sessions()
        now = time.time()
        expired = [t for t, s in sessions.items()
                   if float(s.get("expires", 0)) <= now]
        if expired:
            for t in expired:
                sessions.pop(t, None)
            _write_sessions(sessions)
        rec = sessions.get(token)
        if not rec or float(rec.get("expires", 0)) <= now:
            return None
        return {"username": rec["username"], "role": rec["role"]}


def destroy_session(token: str) -> None:
    with _lock:
        sessions = _read_sessions()
        if token in sessions:
            sessions.pop(token, None)
            _write_sessions(sessions)


# ── RBAC matrix (path-prefix rules, most specific first) ───────────────────
# (method or None for any, path prefix, minimum role level)
_RULES: List[Tuple[Optional[str], str, int]] = [
    # Administrator-only surfaces.
    (None, "/api/auth/", 2),                 # user/security management
    (None, "/api/audit", 2),
    (None, "/api/search/export", 2),
    (None, "/api/report/export", 2),
    (None, "/api/quickmatch/export", 2),
    (None, "/api/task/export", 2),
    (None, "/api/batch/export", 2),
    (None, "/api/quality/export", 2),
    (None, "/api/calibration", 2),
    ("PUT", "/api/intents", 2),
    ("DELETE", "/api/settings", 2),
    ("PUT", "/api/settings", 2),
    (None, "/api/aliases/approve", 2),
    (None, "/api/aliases/reject", 2),
    (None, "/api/feedback/suggestions/apply", 2),
    (None, "/api/feedback/suggestions/reject", 2),
    (None, "/api/synonyms/", 2),
    (None, "/api/shadow/review", 2),
    (None, "/api/preferences/forget", 2),
    (None, "/api/selfimprove", 2),
    # Analyst+ (full reads, no exports).
    (None, "/api/reconcile", 1),
    (None, "/api/batch", 1),
    (None, "/api/report", 1),
    (None, "/api/brief", 1),
    (None, "/api/learn", 1),
    (None, "/api/quality", 1),
    # Everything else: viewer+ (masked reads).
]

EXEMPT_PATHS = {"/api/health", "/api/auth/login"}


def require(path: str, method: str, role: str) -> bool:
    """True when `role` may call `method path` (used by the middleware)."""
    lvl = _LEVEL.get(role, -1)
    if lvl < 0:
        return False
    for m, prefix, min_lvl in _RULES:
        if path.startswith(prefix) and (m is None or m == method.upper()):
            return lvl >= min_lvl
    return lvl >= 0  # default: viewer+


# ── Field-level masking (API boundary, viewer sessions) ────────────────────
def mask_value(value: str) -> str:
    """Partial display for a sensitive field: first 3 + last 2 chars."""
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= 5:
        return s[0] + "*" * max(1, len(s) - 1)
    return s[:3] + "*" * (len(s) - 5) + s[-2:]


def mask_payload(payload: Any) -> Any:
    """Deep-walk a response payload and mask any sensitive-key value
    (applied by the middleware for viewer sessions)."""
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS \
                    and isinstance(v, str):
                out[k] = mask_value(v)
            else:
                out[k] = mask_payload(v)
        return out
    if isinstance(payload, list):
        return [mask_payload(v) for v in payload]
    return payload
