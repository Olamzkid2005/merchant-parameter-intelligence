"""
test_auth.py — Opt-in authN/Z + RBAC + field masking
(docs/technical-review-2026-08-original.md #1, second slice).

Hermetic: MERCHANT_SECURITY_CONFIG / MERCHANT_SESSIONS_FILE point at temp
files, so the shipped data/ files are never touched. Covers password
hashing, config bootstrap, sessions (create/get/destroy/expiry), the RBAC
matrix, and field-level masking.
"""
import os
import sys
import tempfile
import time
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


_tmp = tempfile.mkdtemp(prefix="auth_")
os.environ["MERCHANT_SECURITY_CONFIG"] = str(Path(_tmp) / "security_config.json")
os.environ["MERCHANT_SESSIONS_FILE"] = str(Path(_tmp) / "sessions.json")

from merchant_intelligence import auth  # noqa: E402


def _reset():
    for p in (Path(os.environ["MERCHANT_SECURITY_CONFIG"]),
              Path(os.environ["MERCHANT_SESSIONS_FILE"])):
        try:
            p.unlink()
        except OSError:
            pass


print("\n[1] password hashing")
salt, h = auth.hash_password("correct horse")
check("hash round-trip", auth.verify_password("correct horse", salt, h))
check("wrong password rejected",
      not auth.verify_password("wrong", salt, h))
salt2, h2 = auth.hash_password("correct horse")
check("per-user salts (no identical hashes)", salt != salt2 and h != h2)

print("\n[2] config bootstrap")
_reset()
check("defaults to disabled", auth.enabled() is False)
cfg = auth.load_config()
check("default has empty users + secret",
      cfg["users"] == [] and len(cfg.get("secret", "")) == 64)

print("\n[3] sessions")
_reset()
t = auth.create_session("david", "administrator")
s = auth.get_session(t)
check("session created with role", s == {"username": "david",
                                         "role": "administrator"}, repr(s))
check("unknown token rejected", auth.get_session("nope") is None)
auth.destroy_session(t)
check("destroyed session rejected", auth.get_session(t) is None)
t2 = auth.create_session("alice", "viewer")
_sess = json = None  # noqa: F841
import json as _json  # noqa: E402
path = Path(os.environ["MERCHANT_SESSIONS_FILE"])
data = _json.loads(path.read_text(encoding="utf-8"))
data[t2]["expires"] = time.time() - 1
path.write_text(_json.dumps(data), encoding="utf-8")
check("expired session pruned + rejected", auth.get_session(t2) is None
      and _json.loads(path.read_text(encoding="utf-8")) == {})

print("\n[4] RBAC matrix")
check("viewer can search", auth.require("/api/search", "POST", "viewer"))
check("viewer can profile", auth.require("/api/profile", "POST", "viewer"))
check("viewer CANNOT export",
      not auth.require("/api/search/export", "POST", "viewer"))
check("viewer cannot edit intents",
      not auth.require("/api/intents", "PUT", "viewer"))
check("analyst can batch/reconcile",
      auth.require("/api/batch", "POST", "analyst")
      and auth.require("/api/reconcile", "POST", "analyst"))
check("analyst CANNOT export",
      not auth.require("/api/batch/export", "POST", "analyst")
      and not auth.require("/api/report/export", "POST", "analyst"))
check("analyst cannot edit settings",
      not auth.require("/api/settings", "PUT", "analyst"))
check("admin can export + manage",
      auth.require("/api/task/export", "POST", "administrator")
      and auth.require("/api/intents", "PUT", "administrator")
      and auth.require("/api/audit", "GET", "administrator")
      and auth.require("/api/calibration", "GET", "administrator"))
check("admin can auth-manage",
      auth.require("/api/auth/users", "POST", "administrator"))
check("unknown role denied everywhere",
      not auth.require("/api/search", "POST", "guest"))
check("health is exempt, not role-checked",
      "/api/health" in auth.EXEMPT_PATHS)

print("\n[5] field masking")
check("mask formats 10-digit (3+5+2)",
      auth.mask_value("5180467849") == "518*****49",
      auth.mask_value("5180467849"))
check("mask short value", auth.mask_value("abc") == "a**")
check("mask empty", auth.mask_value("") == "")
payload = {
    "merchant_name": "MEDPLUS",
    "account_number": "5180467849",
    "static_acc_no": "029888",
    "rows": [{"bvn": "222333444", "phone": "08098726020", "email": "a@b.c"}],
}
masked = auth.mask_payload(payload)
check("non-sensitive field untouched",
      masked["merchant_name"] == "MEDPLUS", repr(masked))
check("top-level sensitive masked",
      masked["account_number"] == "518*****49"
      and masked["static_acc_no"] == "029*88",
      repr(masked))
check("nested sensitive masked",
      masked["rows"][0]["bvn"] == "222****44"
      and masked["rows"][0]["phone"] == "080******20"
      and masked["rows"][0]["email"] == "a****",
      repr(masked["rows"][0]))
check("default actor is local", auth.current_actor() == "local")

print("\n============================================================")
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("============================================================")
sys.exit(1 if FAIL else 0)
