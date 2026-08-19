"""governed.py — Governed learned-assets: versioned aliases, rules, thresholds.

Every learned asset (alias, intent pattern, calibration threshold) is now:
  - Versioned (each change creates a new version with a diff)
  - Attributed (who made the change, when, why)
  - Reviewable (proposed → approved → applied lifecycle)
  - Auditable (every state change logged)

This replaces the current implicit "approve and forget" model with a
governed pipeline where every learned asset is a first-class, traceable
object.

Assets managed:
  1. **Aliases** — merchant name aliases (e.g. MEDPLUS → MEDPLUS LIMITED)
  2. **Intent patterns** — regex patterns in intents.json
  3. **Calibration thresholds** — ask/gap thresholds per intent
  4. **Exemplars** — semantic tier exemplar phrases

All state is stored in ``data/asset_history.jsonl`` (append-only ledger)
and ``data/asset_versions/`` (version snapshots).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_VERSIONS_DIR = _DATA_DIR / "asset_versions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AssetType(str, Enum):
    ALIAS = "alias"
    PATTERN = "pattern"
    THRESHOLD = "threshold"
    EXEMPLAR = "exemplar"


class AssetState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"
    RETIRED = "retired"


@dataclass
class AssetVersion:
    asset_type: str
    asset_id: str
    version: int
    state: str
    data: Dict[str, Any]
    created_by: str
    created_at: str
    note: str = ""
    parent_version: Optional[int] = None


@dataclass
class AssetEvent:
    event_type: str       # created | state_changed | data_updated | retired
    asset_type: str
    asset_id: str
    version: int
    old_state: Optional[str]
    new_state: Optional[str]
    actor: str
    ts: str
    details: Dict[str, Any] = field(default_factory=dict)


# ── Ledger ──────────────────────────────────────────────────────────────────

def _ledger_path() -> Path:
    return _DATA_DIR / "asset_history.jsonl"


def _append_event(event: AssetEvent) -> None:
    """Append an event to the asset history ledger."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def _versions_dir() -> Path:
    d = _VERSIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_path(asset_type: str, asset_id: str) -> Path:
    safe_id = hashlib.md5(f"{asset_type}:{asset_id}".encode()).hexdigest()[:12]
    return _versions_dir() / f"{asset_type}_{safe_id}.json"


# ── Version store ───────────────────────────────────────────────────────────

def _load_versions(asset_type: str, asset_id: str) -> List[Dict[str, Any]]:
    """Load all versions for an asset."""
    p = _version_path(asset_type, asset_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_versions(asset_type: str, asset_id: str,
                   versions: List[Dict[str, Any]]) -> None:
    """Save all versions for an asset."""
    p = _version_path(asset_type, asset_id)
    p.write_text(json.dumps(versions, indent=2, ensure_ascii=False),
                 encoding="utf-8")


# ── Public API ──────────────────────────────────────────────────────────────

def propose_asset(asset_type: str, asset_id: str, data: Dict[str, Any],
                  actor: str = "system", note: str = "") -> Dict[str, Any]:
    """Create a new asset version in 'proposed' state."""
    versions = _load_versions(asset_type, asset_id)
    version_num = len(versions) + 1

    v = AssetVersion(
        asset_type=asset_type,
        asset_id=asset_id,
        version=version_num,
        state=AssetState.PROPOSED,
        data=data,
        created_by=actor,
        created_at=_now_iso(),
        note=note,
        parent_version=version_num - 1 if version_num > 1 else None,
    )
    versions.append(asdict(v))
    _save_versions(asset_type, asset_id, versions)

    _append_event(AssetEvent(
        event_type="created",
        asset_type=asset_type,
        asset_id=asset_id,
        version=version_num,
        old_state=None,
        new_state=AssetState.PROPOSED,
        actor=actor,
        ts=_now_iso(),
        details={"data": data},
    ))

    return {"ok": True, "version": version_num, "state": AssetState.PROPOSED}


def approve_asset(asset_type: str, asset_id: str, version: int,
                  actor: str = "system") -> Dict[str, Any]:
    """Approve a proposed asset version."""
    versions = _load_versions(asset_type, asset_id)
    if version > len(versions):
        return {"ok": False, "error": f"Version {version} not found"}

    v = versions[version - 1]
    old_state = v["state"]
    if old_state != AssetState.PROPOSED:
        return {"ok": False, "error": f"Cannot approve: current state is {old_state}"}

    v["state"] = AssetState.APPROVED
    _save_versions(asset_type, asset_id, versions)

    _append_event(AssetEvent(
        event_type="state_changed",
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        old_state=old_state,
        new_state=AssetState.APPROVED,
        actor=actor,
        ts=_now_iso(),
    ))

    return {"ok": True, "version": version, "state": AssetState.APPROVED}


def apply_asset(asset_type: str, asset_id: str, version: int,
                actor: str = "system") -> Dict[str, Any]:
    """Apply an approved asset (actually write it to the config/data)."""
    versions = _load_versions(asset_type, asset_id)
    if version > len(versions):
        return {"ok": False, "error": f"Version {version} not found"}

    v = versions[version - 1]
    old_state = v["state"]
    if old_state != AssetState.APPROVED:
        return {"ok": False, "error": f"Cannot apply: current state is {old_state}"}

    v["state"] = AssetState.APPLIED
    v["applied_at"] = _now_iso()
    _save_versions(asset_type, asset_id, versions)

    _append_event(AssetEvent(
        event_type="state_changed",
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        old_state=old_state,
        new_state=AssetState.APPLIED,
        actor=actor,
        ts=_now_iso(),
    ))

    # Apply the asset to its target
    _apply_to_target(asset_type, asset_id, v["data"])

    return {"ok": True, "version": version, "state": AssetState.APPLIED}


def reject_asset(asset_type: str, asset_id: str, version: int,
                 actor: str = "system", reason: str = "") -> Dict[str, Any]:
    """Reject a proposed asset version."""
    versions = _load_versions(asset_type, asset_id)
    if version > len(versions):
        return {"ok": False, "error": f"Version {version} not found"}

    v = versions[version - 1]
    old_state = v["state"]
    v["state"] = AssetState.REJECTED
    v["rejection_reason"] = reason
    _save_versions(asset_type, asset_id, versions)

    _append_event(AssetEvent(
        event_type="state_changed",
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        old_state=old_state,
        new_state=AssetState.REJECTED,
        actor=actor,
        ts=_now_iso(),
        details={"reason": reason},
    ))

    return {"ok": True, "version": version, "state": AssetState.REJECTED}


def get_asset_history(asset_type: Optional[str] = None,
                      limit: int = 50) -> List[Dict[str, Any]]:
    """Read recent asset events from the ledger."""
    path = _ledger_path()
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if asset_type and event.get("asset_type") != asset_type:
                continue
            events.append(event)
            if len(events) >= limit:
                break
        except json.JSONDecodeError:
            pass
    return events


def get_pending_assets(asset_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Find all assets in 'proposed' state that need review."""
    pending = []
    if not _versions_dir().exists():
        return pending

    for p in _versions_dir().glob("*.json"):
        try:
            versions = json.loads(p.read_text(encoding="utf-8"))
            for v in versions:
                if v.get("state") == AssetState.PROPOSED:
                    if asset_type and v.get("asset_type") != asset_type:
                        continue
                    pending.append(v)
        except Exception:
            pass
    return pending


# ── Apply to target ─────────────────────────────────────────────────────────

def _apply_to_target(asset_type: str, asset_id: str, data: Dict[str, Any]) -> None:
    """Actually write the asset to its target (config file, data store, etc.)."""
    if asset_type == AssetType.ALIAS:
        _apply_alias(asset_id, data)
    elif asset_type == AssetType.PATTERN:
        _apply_pattern(asset_id, data)
    elif asset_type == AssetType.THRESHOLD:
        _apply_threshold(asset_id, data)
    elif asset_type == AssetType.EXEMPLAR:
        _apply_exemplar(asset_id, data)


def _apply_alias(alias_id: str, data: Dict[str, Any]) -> None:
    """Write an alias to the aliases table."""
    import sqlite3
    db = _DATA_DIR / "intelligence.db"
    if not db.exists():
        return
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO aliases (canonical, alias, weight, source) "
            "VALUES (?, ?, ?, ?)",
            (data.get("canonical", ""), data.get("alias", ""),
             data.get("weight", 1), "governed"))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _apply_pattern(pattern_id: str, data: Dict[str, Any]) -> None:
    """Write a pattern to intents.json via the existing save_intent_config."""
    try:
        from merchant_intelligence.tasks.vocab import save_intent_config
        save_intent_config(data.get("intent", ""), {
            "patterns": data.get("patterns", []),
            "keywords": data.get("keywords", []),
        })
    except Exception:
        pass


def _apply_threshold(threshold_id: str, data: Dict[str, Any]) -> None:
    """Write a threshold to engine_settings.json."""
    try:
        from merchant_intelligence import settings
        current = settings.load()
        for key, value in data.items():
            current[key] = value
        settings.save(current)
    except Exception:
        pass


def _apply_exemplar(exemplar_id: str, data: Dict[str, Any]) -> None:
    """Write exemplars to exemplars.json."""
    path = _DATA_DIR / "exemplars.json"
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"intents": {}}
        intent = data.get("intent", "")
        phrases = data.get("phrases", [])
        if intent not in existing["intents"]:
            existing["intents"][intent] = []
        for p in phrases:
            if p not in existing["intents"][intent]:
                existing["intents"][intent].append(p)
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception:
        pass


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(_PROJECT_ROOT))

    print("Governed Assets — Status")
    print("=" * 40)

    pending = get_pending_assets()
    print(f"\nPending review: {len(pending)}")
    for p in pending[:10]:
        print(f"  [{p['asset_type']}] {p['asset_id']} v{p['version']} "
              f"— {p.get('note', 'no note')}")

    events = get_asset_history(limit=10)
    print(f"\nRecent events: {len(events)}")
    for e in events:
        print(f"  [{e['event_type']}] {e['asset_type']}/{e['asset_id']} "
              f"v{e['version']} — {e.get('old_state', '?')} → {e.get('new_state', '?')} "
              f"by {e.get('actor', '?')}")
