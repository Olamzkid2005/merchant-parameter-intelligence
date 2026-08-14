"""
settings.py — runtime-tunable engine settings.

Settings that used to be Python constants are now overridable without a
restart, following the same precedence as the other config files:

    1. Env var  (e.g. DECISIVE_MATCH_THRESHOLD=90)
    2. data/engine_settings.json   ({"decisive_match_threshold": 90})
    3. Built-in default            (config.DECISIVE_MATCH_THRESHOLD)

The Rule Engine page reads / writes this file through the /api/settings
endpoints, so non-developers can tune the engine without touching code.
The settings file is re-read on every access (no process restart needed);
a tiny lock keeps concurrent writes safe (FastAPI runs sync handlers in a
threadpool).

Settings:
    decisive_match_threshold  — name-search score (0-100, ~8.5/10) at which a
                                decisive winner's profile only expands from
                                same-merchant records (see profile.py).
"""

import json
import os
import threading
from typing import Any, Dict, Optional

from . import config

_lock = threading.Lock()

# Validatable knobs: name -> (min, max, cast)
_SPEC = {
    "decisive_match_threshold": (0.0, 100.0, float),
}


def _path() -> Any:
    """Settings file path — override via ENGINE_SETTINGS_FILE env var
    (tests use a temp file so the real config is untouched)."""
    override = os.environ.get("ENGINE_SETTINGS_FILE")
    if override:
        return __import__("pathlib").Path(override)
    return config.DATA_DIR / "engine_settings.json"


def load() -> Dict[str, Any]:
    """Read the settings file. Corrupt/missing files fall back to {}."""
    try:
        path = _path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save(data: Dict[str, Any]) -> None:
    """Atomically write a settings dict to the file (thread-safe)."""
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)


def effective(name: str) -> Optional[float]:
    """Resolve one knob: env var > settings file > built-in default.

    A value is only accepted when it falls inside the knob's valid range —
    out-of-range or unparseable values are ignored (default wins).
    """
    lo, hi, cast = _SPEC.get(name, (None, None, float))
    if lo is None:
        return None
    # 1. Env var
    env = os.environ.get(name.upper())
    if env is not None:
        try:
            val = cast(env)
            if lo <= val <= hi:
                return val
        except (TypeError, ValueError):
            pass
    # 2. Settings file
    file_val = load().get(name)
    if isinstance(file_val, (int, float)):
        try:
            val = cast(file_val)
            if lo <= val <= hi:
                return val
        except (TypeError, ValueError):
            pass
    # 3. Built-in default (the config constant is UPPERCASE, e.g.
    #    DECISIVE_MATCH_THRESHOLD while the knob name is lowercase)
    default = getattr(config, name.upper(), None)
    return cast(default) if default is not None else None


def all_settings() -> Dict[str, Any]:
    """Resolved values + defaults + source for every tunable knob."""
    out: Dict[str, Any] = {}
    for name in _SPEC:
        out[name] = {
            "value": effective(name),
            "default": getattr(config, name.upper(), None),
            "source": _source(name),
            "valid_range": list(_SPEC[name][:2]),
        }
    return out


def _source(name: str) -> str:
    env = os.environ.get(name.upper())
    if env is not None:
        return "env var"
    if name in load():
        return str(_path())
    return "built-in default"


def decisive_match_threshold() -> float:
    """Convenience: the resolved decisive-match threshold."""
    return float(effective("decisive_match_threshold") or
                 config.DECISIVE_MATCH_THRESHOLD)
