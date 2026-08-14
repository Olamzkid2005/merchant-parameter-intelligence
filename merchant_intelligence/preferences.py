"""
preferences.py — Remembered clarification choices.

When the user answers an ambiguous request ("account details" could mean the
profile, the static account or the change history) and checks "remember my
choice", the normalized request phrase -> intent mapping is stored in
data/clarification_preferences.json. Future requests with the same
normalized phrase skip the clarification card and auto-run the saved intent
(suggest_clarification returns auto_pick).

The phrase key is normalized so merchant names, identifiers and request
filler words never pollute it: "get account details for medplus" and "get
the account details of lagoons" share the key "account details". Override
the file with the MERCHANT_PREFERENCES_FILE env var (tests use a temp file).
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Request filler / instruction words that carry no meaning for the phrase
# key ("get account details for medplus" -> "account details").
_PHRASE_STOP_WORDS = {
    "a", "an", "and", "any", "all", "are", "about", "above", "also",
    "below", "can", "could", "do", "for", "from", "get", "give", "given",
    "help", "i", "in", "info", "into", "is", "it", "its", "kindly", "me",
    "my", "need", "of", "on", "or", "out", "please", "pls", "provide",
    "show", "some", "than", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "to", "use", "used", "want", "we",
    "what", "which", "with", "would", "you", "your",
}

# Identifier shapes stripped before keying (MX codes, TIDs, phones, emails,
# account/bvn/mid strings, 2ISW ids) — they belong to the merchant, not the
# request phrase.
_IDENTIFIER_RE = re.compile(
    r"\b(?:MX\d{4,8}\b|\d{4}[A-Z]\d{3}\b|(?:[+]?234|0)[789]\d{9}\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\d{6,12}\b|"
    r"2ISW[A-Z0-9]{4,}\b)",
    re.IGNORECASE,
)


def _path() -> Path:
    override = os.environ.get("MERCHANT_PREFERENCES_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "clarification_preferences.json"


def load() -> Dict[str, str]:
    """phrase key -> intent. Corrupt files fall back to {}."""
    path = _path()
    if not path.exists():
        return {}
    try:
        with _lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save(data: Dict[str, str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _mutate(mutator) -> None:
    """Load -> mutate -> save while holding the lock the WHOLE time.

    learn/forget do a read-modify-write cycle; if the lock were released
    between the load and the save, two concurrent /api/task calls (FastAPI
    runs sync handlers in a threadpool) could interleave and one write would
    silently overwrite the other's change. Holding one lock across the whole
    cycle makes the store race-free.
    """
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}
        mutator(data)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def phrase_key(text: str, task: Optional[Dict[str, Any]] = None) -> str:
    """Stable normalized key for a request: strips identifiers, the merchant
    name and filler words so the SAME ambiguous phrase across different
    merchants shares one key (\"get account details for medplus\" and \"get
    the account details of lagoons\" both key to \"account details\")."""
    low = _IDENTIFIER_RE.sub(" ", (text or "").lower())
    # Strip the merchant name(s) the engine extracted from the request.
    for n in (task or {}).get("names") or []:
        low = low.replace(str(n).lower(), " ")
    for pair in (task or {}).get("named") or []:
        low = low.replace(str(pair.get("name", "")).lower(), " ")
    tokens = [w for w in low.split() if w and w not in _PHRASE_STOP_WORDS]
    return " ".join(tokens)


def learn(text: str, intent: str, task: Optional[Dict[str, Any]] = None) -> str:
    """Save phrase -> intent. Returns the key that was stored."""
    key = phrase_key(text, task)
    if not key or not intent:
        return ""
    _mutate(lambda data: data.__setitem__(key, intent))
    logger.info("preference_learned key=%r intent=%s", key, intent)
    return key


def lookup(text: str, task: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The remembered intent for this phrase, or None."""
    key = phrase_key(text, task)
    return load().get(key) if key else None


def all_prefs() -> Dict[str, str]:
    return dict(load())


def forget(key: str) -> bool:
    """Remove a saved phrase key. Returns True if it existed."""
    removed = [False]

    def _do(data: Dict[str, str]) -> None:
        if key in data:
            data.pop(key)
            removed[0] = True

    _mutate(_do)
    if removed[0]:
        logger.info("preference_forgotten key=%r", key)
    return removed[0]


def reset() -> int:
    """Clear every saved preference. Returns the number removed."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            n = len(data) if isinstance(data, dict) else 0
        except (OSError, json.JSONDecodeError, ValueError):
            n = 0
        try:
            path.write_text("{}\n", encoding="utf-8")
        except OSError:
            pass
    return n
