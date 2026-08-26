"""
test_watcher.py — incremental ingestion watch mode (merchant_intelligence/
watcher.py), hermetic: no real rebuild subprocess is ever launched.

Covers:
  1. Trigger decision logic — fresh sources don't trigger; stale sources do;
     unsettled (recently modified) sources debounce; cooldown suppresses.
  2. Rebuild orchestration — scripts run in order, first failure aborts the
     pipeline, the singleton reset hook is invoked BEFORE any script, the
     harness runs non-fatal (its failure doesn't fail the rebuild), and the
     log file is written.
  3. Status shape + manual trigger queueing.
  4. api_shared.reset_shared_singletons() — closes held DB connections and
     clears the cache so the next get_* lazily reconnects.

Run:  python -X utf-8 tests/test_watcher.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from merchant_intelligence import watcher as wmod
from merchant_intelligence.watcher import IngestWatcher

checks = 0
fails = 0


def check(name, cond, detail=""):
    global checks, fails
    checks += 1
    mark = "ok" if cond else "FAIL"
    if not cond:
        fails += 1
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


def make_watcher(**env):
    """Watcher with fast timings, isolated from real env/config."""
    saved = {k: os.environ.get(k) for k in
             ("INGEST_WATCH_INTERVAL", "INGEST_WATCH_SETTLE",
              "INGEST_WATCH_COOLDOWN")}
    for k, v in env.items():
        os.environ[k] = str(v)
    try:
        w = IngestWatcher()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return w


# ── 1. freshness patching helper ─────────────────────────────────────────
#
# _poll_once does `from . import ingest_ledger`, which resolves the attribute
# on the merchant_intelligence PACKAGE — so that's what must be patched
# (patching the watcher module's own attribute has no effect).

import merchant_intelligence
import merchant_intelligence.ingest_ledger  # noqa: F401 — sets the pkg attr


class FakeLedger:
    def __init__(self, stale, fresh=False):
        self.stale = stale
        self.fresh_flag = fresh
        self.recorded = []

    def freshness(self):
        return {"fresh": self.fresh_flag, "stale_sources": self.stale,
                "db_rows": 1, "source_count": 1}

    def record(self, *a, **k):
        self.recorded.append((a, k))


def patch_ledger(fake):
    merchant_intelligence.ingest_ledger = fake


def unpatch_ledger(saved):
    merchant_intelligence.ingest_ledger = saved


def old_stale(n=1, age=100):
    """Stale entries whose mtime is `age` seconds old (settled)."""
    return [{"name": f"data/file{i}.xlsx", "mtime_ns": int((time.time() - age) * 1e9),
             "size": 123, "status": "changed"} for i in range(n)]


def new_stale(n=1):
    """Stale entries modified right now (unsettled)."""
    return old_stale(n, age=0)


print("== 1. trigger decision logic ==")

_saved_ledger = merchant_intelligence.ingest_ledger
# Safety: never let a decision test reach the REAL rebuild.
_rebuild_calls = []
w_default_rebuild = IngestWatcher._rebuild
IngestWatcher._rebuild = lambda self, stale: _rebuild_calls.append(stale)

w = make_watcher(INGEST_WATCH_SETTLE=20, INGEST_WATCH_COOLDOWN=600)
fake = FakeLedger([], fresh=True)
patch_ledger(fake)
w._poll_once()
check("fresh sources -> state watching, no rebuild",
      w.state == "watching" and w.last_rebuild is None, w.state)

w = make_watcher(INGEST_WATCH_SETTLE=20, INGEST_WATCH_COOLDOWN=600)
fake = FakeLedger(new_stale(2))
patch_ledger(fake)
w._poll_once()
check("unsettled (just-saved) sources debounce — no rebuild",
      w.state == "watching" and w.last_rebuild is None, w.state)

w = make_watcher(INGEST_WATCH_SETTLE=20, INGEST_WATCH_COOLDOWN=600)
fake = FakeLedger(old_stale(1))
patch_ledger(fake)
w._poll_once()
check("settled stale sources trigger rebuild",
      len(_rebuild_calls) == 1 and _rebuild_calls[0] == fake.stale,
      f"calls={len(_rebuild_calls)}")

# cooldown: second poll right after a rebuild is suppressed
w._last_rebuild_finished = time.time()
w._poll_once()
check("cooldown suppresses an immediate second rebuild",
      len(_rebuild_calls) == 1, f"calls={len(_rebuild_calls)}")

# cooldown expiry allows the next rebuild
w._last_rebuild_finished = time.time() - 601
w._poll_once()
check("cooldown expiry allows the next rebuild",
      len(_rebuild_calls) == 2, f"calls={len(_rebuild_calls)}")

# restore the real _rebuild for section 2 (which re-patches _run_script)
IngestWatcher._rebuild = w_default_rebuild
unpatch_ledger(_saved_ledger)

# ── 2. rebuild orchestration ─────────────────────────────────────────────
print("== 2. rebuild orchestration ==")

w = make_watcher()
reset_calls = []

import types
fake_api_shared = types.ModuleType("api_shared")
fake_api_shared.reset_shared_singletons = lambda: reset_calls.append(1)
saved_api_shared = sys.modules.get("api_shared")
sys.modules["api_shared"] = fake_api_shared

script_calls = []


def fake_run_script(self, script, log):
    script_calls.append(script[0])
    # rebuild_db.py fails -> pipeline aborts before the other scripts
    return 1 if "rebuild_db" in script[0] else 0


tmpdir = tempfile.mkdtemp()
saved_root = wmod._PROJECT_ROOT
wmod._PROJECT_ROOT = Path(tmpdir)
IngestWatcher._run_script = fake_run_script
try:
    w._rebuild(old_stale(1))
finally:
    IngestWatcher._run_script = wmod.IngestWatcher._run_script  # restore
    sys.modules.pop("api_shared", None)
    if saved_api_shared is not None:
        sys.modules["api_shared"] = saved_api_shared

check("singleton reset happens BEFORE any rebuild script",
      len(reset_calls) == 1 and len(script_calls) >= 1,
      f"reset={len(reset_calls)} scripts={script_calls}")
check("first failing script aborts the pipeline (harness skipped)",
      script_calls == ["scripts/rebuild_db.py"], str(script_calls))
check("failed rebuild -> state error + last_rebuild.ok False",
      w.state == "error" and w.last_rebuild and w.last_rebuild["ok"] is False,
      f"state={w.state}")
log_file = Path(tmpdir) / "data" / "watch_rebuild.log"
check("rebuild log written", log_file.exists() and
      "watch rebuild" in log_file.read_text(encoding="utf-8"),
      f"exists={log_file.exists()}")

# success path: all three scripts + harness run, harness failure is non-fatal
w2 = make_watcher()
reset_calls.clear()
script_calls.clear()


def fake_run_ok(self, script, log):
    script_calls.append(script[0])
    return 1 if "self_improve" in script[0] else 0  # harness fails


IngestWatcher._run_script = fake_run_ok
try:
    w2._rebuild(old_stale(1))
finally:
    IngestWatcher._run_script = wmod.IngestWatcher._run_script
wmod._PROJECT_ROOT = saved_root

check("success path runs all 3 scripts + harness",
      script_calls == ["scripts/rebuild_db.py",
                       "scripts/build_intelligence_db.py",
                       "scripts/sync_intel_db.py",
                       "scripts/self_improve.py"], str(script_calls))
check("harness failure is non-fatal (rebuild ok)",
      w2.last_rebuild and w2.last_rebuild["ok"] is True
      and w2.last_rebuild["harness_ok"] is False,
      str(w2.last_rebuild))
check("successful rebuild -> state watching",
      w2.state == "watching", w2.state)

# ── 3. status + trigger ──────────────────────────────────────────────────
print("== 3. status + manual trigger ==")

s = w2.status()
for key in ("enabled", "state", "interval_secs", "settle_secs",
            "cooldown_secs", "last_check_at", "stale_sources",
            "last_rebuild", "last_error"):
    check(f"status has {key}", key in s)

w3 = make_watcher()
r = w3.trigger("test")
check("trigger queues an immediate wake", r.get("queued") is True
      and w3._wake.is_set())

# env config parsing
w4 = make_watcher(INGEST_WATCH_INTERVAL="5", INGEST_WATCH_SETTLE="2",
                  INGEST_WATCH_COOLDOWN="30")
check("env config parsed", (w4.interval, w4.settle_secs, w4.cooldown_secs)
      == (5, 2, 30))
w5 = make_watcher()
check("default timings", (w5.interval, w5.settle_secs, w5.cooldown_secs)
      == (30, 20, 600))
os.environ["INGEST_WATCH"] = "0"
w6 = IngestWatcher()
os.environ.pop("INGEST_WATCH", None)
check("INGEST_WATCH=0 disables", w6.enabled is False)

# ── 4. api_shared.reset_shared_singletons ────────────────────────────────
print("== 4. api_shared.reset_shared_singletons ==")

from api_shared import reset_shared_singletons

class FakeDB:
    def __init__(self):
        self.closed = 0
    def close(self):
        self.closed += 1

class FakeSearcher:
    def __init__(self):
        self.db = FakeDB()

import api_shared
import merchant_intelligence as _mi

# Hermetic: patch MerchantSearch so the lazy-recreate check never touches a
# real database (CI has no data/ directory).
class _MarkerSearch(FakeSearcher):
    pass

saved_searcher = api_shared._searcher
saved_ms = _mi.MerchantSearch
try:
    api_shared._searcher = FakeSearcher()
    reset_shared_singletons()
    check("searcher db closed + cache cleared",
          api_shared._searcher is None)
    _mi.MerchantSearch = _MarkerSearch
    s = api_shared.get_searcher()
    check("get_searcher lazily recreates after reset",
          isinstance(s, _MarkerSearch), type(s).__name__)
finally:
    api_shared._searcher = saved_searcher
    _mi.MerchantSearch = saved_ms

# ── 5. singleton module wiring ─────────────────────────────────────────
print("== 5. module wiring ==")

from merchant_intelligence.watcher import get_watcher as gw
check("get_watcher returns the same singleton", gw() is gw())

# ── 6. excluded exports never read as stale (rebuild-loop guard) ────────
print("== 6. excluded exports never read as stale ==")

from merchant_intelligence import ingest_ledger

excl_tmp = tempfile.mkdtemp()
try:
    for name in ("medplus_tids.xlsx", "medplus_mids.xlsx", "real_source.xlsx"):
        (Path(excl_tmp) / name).write_bytes(b"x")
    snap = ingest_ledger._folder_snapshot(Path(excl_tmp))
    snap_names = {p.name for p in snap}
    check("excluded export files are not snapshotted",
          "medplus_tids.xlsx" not in snap_names
          and "medplus_mids.xlsx" not in snap_names, str(snap_names))
    check("real source files still snapshotted",
          "real_source.xlsx" in snap_names, str(snap_names))
finally:
    import shutil as _sh
    _sh.rmtree(excl_tmp, ignore_errors=True)

print()
print(f"RESULT: {checks - fails}/{checks} checks passed")
sys.exit(1 if fails else 0)
