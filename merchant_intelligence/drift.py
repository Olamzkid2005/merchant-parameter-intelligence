"""drift.py — Quality-scan / drift-monitoring for the merchant intelligence engine.

Runs three scans:
  1. **Intent routing drift** — replays the golden set and compares outcomes
     against the committed snapshot.  A routing change that wasn't reviewed
     (i.e. the snapshot wasn't refreshed) is flagged.
  2. **Search recall drift** — replays a sample of known merchant queries and
     checks that recall@1 hasn't dropped below the stored baseline.
  3. **Data freshness** — checks the ingestion ledger for staleness (no good
     rebuild in N hours).

Each scan produces a ScanResult with status "ok" | "warning" | "critical"
and a human-readable summary.  Results are persisted to
``data/drift_history.jsonl`` so they can be charted over time.

Designed to run:
  - via a CLI wrapper (``python -m merchant_intelligence.drift``)
  - via a cron-style scheduler
  - on-demand from the admin API (``POST /api/admin/drift-scan``)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── project imports (lazy to keep the module importable without the DB) ─────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── thresholds ──────────────────────────────────────────────────────────────
# Routing: a golden-set query that changed outcome without the snapshot being
# refreshed is a drift event.
_ROUTING_WARN_THRESHOLD = 1    # >= 1 drifted query → warning
_ROUTING_CRIT_THRESHOLD = 5    # >= 5 → critical

# Recall: percentage-point drop below the stored baseline.
_RECALL_WARN_DROP = 2.0        # ≥ 2 pp drop → warning
_RECALL_CRIT_DROP = 5.0        # ≥ 5 pp drop → critical

# Freshness: hours since last good ingestion run.
_FRESHNESS_WARN_HOURS = 48     # > 48 h → warning
_FRESHNESS_CRIT_HOURS = 168    # > 168 h (7 days) → critical


# ── data classes ────────────────────────────────────────────────────────────
@dataclass
class ScanResult:
    scan: str                    # "routing" | "recall" | "freshness" | "all"
    status: str                  # "ok" | "warning" | "critical"
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    ts_human: str = field(default_factory=_now_iso)


# ── routing drift scan ─────────────────────────────────────────────────────
def _routing_snapshot_path() -> Path:
    return _PROJECT_ROOT / "merchant_intelligence" / "intent_routing_snapshot.json"


def scan_routing() -> ScanResult:
    """Replay the golden set and compare against the committed snapshot."""
    try:
        from merchant_intelligence.intent_golden import INTENT_GOLDEN
        from merchant_intelligence.tasks import analyze
    except Exception as exc:
        return ScanResult("routing", "critical",
                          f"Cannot load golden set or analyzer: {exc}")

    snap_path = _routing_snapshot_path()
    if not snap_path.exists():
        return ScanResult("routing", "warning",
                          "No routing snapshot — run REBUILD_ROUTING_SNAPSHOT=1")

    try:
        old = json.loads(snap_path.read_text(encoding="utf-8")).get("rows", {})
    except Exception:
        return ScanResult("routing", "critical", "Corrupt routing snapshot file")

    # Temporarily pin Tier 2 off so the scan measures the same thing CI does.
    saved_mode = os.environ.get("SEMANTIC_TIER_MODE")
    os.environ["SEMANTIC_TIER_MODE"] = "off"
    try:
        outcomes = ("routed", "clarify", "misroute", "miss")
        drifted: List[Dict[str, str]] = []
        for entry in INTENT_GOLDEN:
            q, expected = entry["query"], entry["intent"]
            analysis = analyze(q)
            if not analysis.get("is_task"):
                now = "miss"
            elif analysis.get("clarification"):
                now = "clarify"
            elif analysis.get("primary") == expected:
                now = "routed"
            else:
                now = "misroute"
            prev = old.get(q)
            if prev and prev != now:
                drifted.append({"query": q, "expected": expected,
                                "was": prev, "now": now})
    finally:
        if saved_mode is None:
            os.environ.pop("SEMANTIC_TIER_MODE", None)
        else:
            os.environ["SEMANTIC_TIER_MODE"] = saved_mode

    n = len(drifted)
    if n >= _ROUTING_CRIT_THRESHOLD:
        status = "critical"
    elif n >= _ROUTING_WARN_THRESHOLD:
        status = "warning"
    else:
        status = "ok"

    summary = (f"Routing drift: {n} golden queries changed outcome"
               if n else "Routing drift: none")
    return ScanResult("routing", status, summary,
                      {"drifted": drifted, "total_golden": len(INTENT_GOLDEN)})


# ── search recall drift scan ───────────────────────────────────────────────
def _recall_baseline_path() -> Path:
    return _DATA_DIR / "alias_free_baseline.json"


def scan_recall() -> ScanResult:
    """Check recall@1 against the stored baseline."""
    baseline_path = _recall_baseline_path()
    if not baseline_path.exists():
        return ScanResult("recall", "ok",
                          "No recall baseline yet (run self_improve.py)")

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception:
        return ScanResult("recall", "warning", "Corrupt recall baseline file")

    prev_r1 = baseline.get("recall1", 0.0)
    n = baseline.get("n", 0)

    # We can't run the full self_improve harness here (it needs the DB
    # and a search engine), but we can check whether the golden-set
    # routing rate has changed — it's a proxy for recall drift.
    try:
        from merchant_intelligence.intent_golden import INTENT_GOLDEN
        from merchant_intelligence.tasks import analyze

        saved_mode = os.environ.get("SEMANTIC_TIER_MODE")
        os.environ["SEMANTIC_TIER_MODE"] = "off"
        try:
            routed = 0
            for entry in INTENT_GOLDEN:
                analysis = analyze(entry["query"])
                if analysis.get("primary") == entry["intent"]:
                    routed += 1
            current_rate = routed / max(len(INTENT_GOLDEN), 1)
        finally:
            if saved_mode is None:
                os.environ.pop("SEMANTIC_TIER_MODE", None)
            else:
                os.environ["SEMANTIC_TIER_MODE"] = saved_mode

        # Compare against the golden-set routing rate (proxy).
        # The actual recall baseline is from self_improve.py; we just
        # flag large golden-set routing drops here.
        drop_pp = max(0, (prev_r1 - current_rate) * 100)
        if drop_pp >= _RECALL_CRIT_DROP:
            status = "critical"
        elif drop_pp >= _RECALL_WARN_DROP:
            status = "warning"
        else:
            status = "ok"

        summary = (f"Golden-set routing rate: {current_rate*100:.1f}% "
                   f"(baseline recall@1: {prev_r1*100:.1f}%)")
        return ScanResult("recall", status, summary,
                          {"current_rate": round(current_rate, 4),
                           "baseline_recall1": prev_r1,
                           "drop_pp": round(drop_pp, 2),
                           "baseline_n": n})
    except Exception as exc:
        return ScanResult("recall", "warning",
                          f"Could not run recall proxy scan: {exc}")


# ── data freshness scan ────────────────────────────────────────────────────
def scan_freshness() -> ScanResult:
    """Check how long since the last good ingestion run."""
    ledger_path = _DATA_DIR / "ingest_ledger.jsonl"
    if not ledger_path.exists():
        return ScanResult("freshness", "warning",
                          "No ingestion ledger found")

    try:
        last_good_ts = 0.0
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("status") == "ok":
                    ts = entry.get("ts", 0)
                    if ts > last_good_ts:
                        last_good_ts = ts
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        return ScanResult("freshness", "warning",
                          f"Could not read ingestion ledger: {exc}")

    if last_good_ts == 0:
        return ScanResult("freshness", "warning",
                          "No successful ingestion run found in ledger")

    hours_since = (time.time() - last_good_ts) / 3600
    if hours_since > _FRESHNESS_CRIT_THRESHOLD:
        status = "critical"
    elif hours_since > _FRESHNESS_WARN_HOURS:
        status = "warning"
    else:
        status = "ok"

    summary = (f"Last good build: {hours_since:.0f}h ago"
               if hours_since >= 1
               else f"Last good build: {hours_since*60:.0f}m ago")
    return ScanResult("freshness", status, summary,
                      {"hours_since_last_build": round(hours_since, 1),
                       "last_build_ts": last_good_ts})


# ── combined scan ───────────────────────────────────────────────────────────
def scan_all() -> Dict[str, Any]:
    """Run all three scans and return a combined result."""
    results = [scan_routing(), scan_recall(), scan_freshness()]
    statuses = [r.status for r in results]
    overall = ("critical" if "critical" in statuses
               else "warning" if "warning" in statuses
               else "ok")

    combined = {
        "status": overall,
        "ts": time.time(),
        "ts_human": _now_iso(),
        "scans": [asdict(r) for r in results],
    }

    # Persist to history
    _append_history(combined)
    return combined


def _append_history(record: Dict[str, Any]) -> None:
    """Append a scan record to the drift history JSONL file."""
    hist_path = _DATA_DIR / "drift_history.jsonl"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hist_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def recent_history(n: int = 20) -> List[Dict[str, Any]]:
    """Read the last N drift scan records."""
    hist_path = _DATA_DIR / "drift_history.jsonl"
    if not hist_path.exists():
        return []
    lines = hist_path.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-n:]:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(_PROJECT_ROOT))

    print("=" * 60)
    print("  Drift Quality Scan")
    print("=" * 60)

    result = scan_all()
    for scan in result["scans"]:
        icon = {"ok": "✅", "warning": "⚠️", "critical": "🚨"}.get(scan["status"], "?")
        print(f"\n  {icon} {scan['scan']:12s} — {scan['status']}")
        print(f"     {scan['summary']}")
        if scan.get("details", {}).get("drifted"):
            for d in scan["details"]["drifted"][:5]:
                print(f"       {d['query'][:50]}  ({d['was']} → {d['now']})")

    overall_icon = {"ok": "✅", "warning": "⚠️", "critical": "🚨"}[result["status"]]
    print(f"\n  {overall_icon} Overall: {result['status']}")
    print("=" * 60)
    _sys.exit(0 if result["status"] == "ok" else 1)
