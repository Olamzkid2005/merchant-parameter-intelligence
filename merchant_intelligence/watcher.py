"""watcher.py — incremental ingestion watch mode (roadmap #2).

Watches ``data/`` for new or changed Excel workbooks and auto-rebuilds all
three merchant databases when any source drifts from the last good build,
so "drop a workbook in data/" is the whole workflow — no manual
``app.start rebuild`` step.

Why a thread inside the API process?
    The rebuild scripts DELETE the database files, which on Windows is
    impossible while a connection is held open — and the API holds one open
    per cached singleton (searcher / profiler / resolver). The watcher lives
    in the same process precisely so it can close those connections, run the
    rebuild subprocesses, and reset the singletons so the next request
    lazily reconnects to the fresh databases. A separate watcher process
    could never unlock files owned by the API.

Trigger conditions (ALL must hold, evaluated each poll):
    1. ``ingest_ledger.freshness()`` reports at least one NEW or CHANGED
       source vs the last good build (mtime_ns + size snapshot — cheap, no
       hashing of ~100 MB workbooks every 30 s).
    2. Every stale file is SETTLED: its mtime is at least
       ``INGEST_WATCH_SETTLE`` seconds old, so a workbook mid-copy or
       mid-save never triggers a partial-file rebuild.
    3. The cooldown has elapsed since the last rebuild finished
       (``INGEST_WATCH_COOLDOWN``) — protects against rebuild storms while
       several workbooks are dropped one after another.
    4. No rebuild is already running.

Rebuild pipeline (same steps as ``app.start rebuild``, run directly because
``rebuild_databases()`` fails fast while services are up — the watcher is
the component that unlocks the files, so it owns the pipeline):
    1. scripts/rebuild_db.py           -> merchant_search.db
    2. scripts/build_intelligence_db.py -> intelligence.db (records its own
                                          ingest-ledger entry with the fresh
                                          source snapshot, so freshness()
                                          flips to clean automatically)
    3. scripts/sync_intel_db.py         -> merchant_intel.db
    4. scripts/self_improve.py          -> alias-free harness, NON-fatal here
       (a harness regression must not loop the watcher into endless
       rebuilds — it is reported, not failed).

Configuration (env vars):
    INGEST_WATCH=0            disable the watcher entirely (default on)
    INGEST_WATCH_INTERVAL=30  poll seconds
    INGEST_WATCH_SETTLE=20    file-must-be-this-old seconds before rebuild
    INGEST_WATCH_COOLDOWN=600 minimum seconds between rebuilds

Endpoints (admin router): GET /api/ingest/watch, POST /api/ingest/watch/trigger.
UI: "Auto-ingestion watch" card on the Audit Trail page.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The three data scripts (order matters — each feeds the next) plus the
# non-fatal harness step. Kept here rather than imported from app.start
# (no .py extension, not importable) to stay the exact same pipeline.
REBUILD_STEPS = [
    ("merchant_search.db", ["scripts/rebuild_db.py"]),
    ("intelligence.db", ["scripts/build_intelligence_db.py"]),
    ("merchant_intel.db", ["scripts/sync_intel_db.py"]),
]
HARNESS_STEP = ("self-improve harness", ["scripts/self_improve.py"])

REBUILD_TIMEOUT_SECS = 1800  # 30 min per script, same as app.start

_lock = threading.Lock()
_watcher: Optional["IngestWatcher"] = None


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class IngestWatcher:
    """Polls the Excel source folder and auto-rebuilds when sources drift."""

    def __init__(self) -> None:
        self.interval = _env_int("INGEST_WATCH_INTERVAL", 30)
        self.settle_secs = _env_int("INGEST_WATCH_SETTLE", 20)
        self.cooldown_secs = _env_int("INGEST_WATCH_COOLDOWN", 600)
        self.enabled = os.environ.get("INGEST_WATCH", "1") != "0"

        self._wake = threading.Event()      # manual / immediate trigger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.state = "idle"                 # idle | watching | rebuilding
        self.last_check_at: Optional[str] = None
        self.last_stale: List[Dict[str, Any]] = []
        self.last_rebuild: Optional[Dict[str, Any]] = None
        self._last_rebuild_finished: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling daemon thread (no-op when already running)."""
        with _lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="ingest-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self, reason: str = "manual") -> Dict[str, Any]:
        """Queue an immediate scan+rebuild on the next loop iteration."""
        self._wake.set()
        return {"queued": True, "reason": reason, "state": self.state}

    # ── polling loop ─────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self.interval)
            self._wake.clear()
            if self._stop.is_set() or not self.enabled:
                continue
            try:
                self._poll_once()
            except Exception as e:  # noqa: BLE001 — the loop must survive
                self.state = "error"
                self.last_error = str(e)
                try:
                    from . import ingest_ledger
                    ingest_ledger.record(
                        "ingest_watch", "failed",
                        detail=f"watcher poll error: {e}")
                except Exception:  # noqa: BLE001
                    pass

    def _poll_once(self) -> None:
        from . import ingest_ledger

        self.last_check_at = _now_iso()
        fresh = ingest_ledger.freshness()
        stale = fresh.get("stale_sources", [])
        self.last_stale = stale

        if fresh.get("fresh", True):
            if self.state == "watching" or self.state == "idle":
                self.state = "watching"
            return
        if self.state == "rebuilding":
            return

        # Settle: every stale file must be untouched for settle_secs so a
        # workbook mid-save never enters the build.
        now = time.time()
        unsettled = [
            s for s in stale
            if (now - s.get("mtime_ns", 0) / 1e9) < self.settle_secs
        ]
        if unsettled:
            self.state = "watching"
            return

        # Cooldown: never rebuild twice within cooldown_secs (manual
        # triggers included — the settle window already debounces saves).
        if self._last_rebuild_finished and \
                (now - self._last_rebuild_finished) < self.cooldown_secs:
            self.state = "watching"
            return

        self._rebuild(stale)

    # ── rebuild ──────────────────────────────────────────────────────────

    def _rebuild(self, stale: List[Dict[str, Any]]) -> None:
        started = _now_iso()
        self.state = "rebuilding"
        result: Dict[str, Any] = {
            "started_at": started,
            "trigger": "auto",
            "sources": [s.get("name") for s in stale],
            "steps": [],
            "ok": False,
        }

        # Release the API's held connections so the scripts can replace the
        # database files on Windows. Lazy import avoids a circular dep
        # (api_shared imports merchant_intelligence at module load).
        try:
            from api_shared import reset_shared_singletons
            reset_shared_singletons()
        except Exception as e:  # noqa: BLE001
            result["singleton_reset_error"] = str(e)

        log_path = _PROJECT_ROOT / "data" / "watch_rebuild.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ok = True
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n===== watch rebuild {started} "
                      f"({len(stale)} stale source(s)) =====\n")
            for name, script in REBUILD_STEPS:
                step_t0 = time.perf_counter()
                code = self._run_script(script, log)
                result["steps"].append({
                    "step": name, "script": script[0],
                    "ok": code == 0,
                    "secs": round(time.perf_counter() - step_t0, 1),
                })
                if code != 0:
                    ok = False
                    log.write(f"[watcher] {name} FAILED (exit {code})\n")
                    break  # pipeline order matters — abort on first failure

            # Harness is informational: a recall regression must not make
            # the watcher consider the rebuild failed (that would loop).
            # Skipped entirely when the data pipeline itself failed.
            if ok:
                harness_code = self._run_script(HARNESS_STEP[1], log)
                result["harness_ok"] = harness_code == 0

        result["ok"] = ok
        result["finished_at"] = _now_iso()
        result["log"] = str(log_path)
        self.last_rebuild = result
        self._last_rebuild_finished = time.time()
        self.state = "watching" if ok else "error"
        if not ok:
            self.last_error = "rebuild failed — see " + str(log_path)

    def _run_script(self, script: List[str], log) -> int:
        """Run one rebuild script with the current interpreter, log output."""
        try:
            r = subprocess.run(
                [sys.executable, *script],
                cwd=str(_PROJECT_ROOT),
                stdout=log, stderr=subprocess.STDOUT,
                timeout=REBUILD_TIMEOUT_SECS,
            )
            return r.returncode
        except Exception as e:  # noqa: BLE001 — timeout/OSError
            log.write(f"[watcher] {' '.join(script)} error: {e}\n")
            return 1

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "interval_secs": self.interval,
            "settle_secs": self.settle_secs,
            "cooldown_secs": self.cooldown_secs,
            "last_check_at": self.last_check_at,
            "stale_sources": self.last_stale,
            "last_rebuild": self.last_rebuild,
            "last_error": getattr(self, "last_error", None),
        }


def get_watcher() -> IngestWatcher:
    """Process-wide watcher singleton."""
    global _watcher
    with _lock:
        if _watcher is None:
            _watcher = IngestWatcher()
        return _watcher
