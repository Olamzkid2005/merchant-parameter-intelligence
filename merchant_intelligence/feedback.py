"""
feedback.py — Self-improving loop for the task intent parser.

Every real request the app executes (task runs + searches) is appended to a
persistent request log (data/requests_log.jsonl). From that log the engine
learns where its routing is wrong:

  outcome tagging
    - overridden   the user corrected a clarification pick (from calibration)
    - rephrased    a request that produced NO rows was re-asked moments later
                   with overlapping identifiers/names (same merchant, new
                   wording) — the first phrasing failed
    - accepted     produced rows and was never corrected (derived on read)
    - abandoned    produced no rows and no follow-up came (derived on read)

  pattern mining
    mine_patterns() scans the corrections (overrides + rephrased task
    requests) for distinctive n-grams that co-occur with the CORRECTED
    intent, and — once an n-gram has >= MIN_PATTERN_SAMPLES corroborating
    samples — suggests it as a new pattern for that intent. Suggestions are
    surfaced in the Rule Engine UI; applying writes them to intents.json
    (hot-reloaded) via the same save path the editor uses.

The log lives in data/requests_log.jsonl — override with the
MERCHANT_FEEDBACK_FILE env var (tests use a temp file). Rejections live in
data/suggestions_rejected.json (MERCHANT_FEEDBACK_REJECTIONS_FILE).
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger(__name__)

# ── Tuning constants ─────────────────────────────────────────────────────
MIN_PATTERN_SAMPLES = 3       # n-gram must appear in this many corrections
REPHRASE_WINDOW = 1200.0      # 20 minutes — how far back to look for a rephrase
MAX_LOG_ENTRIES = 2000        # prune cap
MIN_SUGGESTED_WEIGHT = 4      # lowest weight for a mined suggestion
MAX_SUGGESTED_WEIGHT = 7      # highest
MAX_SUGGESTIONS = 50           # UI cap

_lock = threading.Lock()


# ── Paths ─────────────────────────────────────────────────────────────────
def _log_path() -> Path:
    override = os.environ.get("MERCHANT_FEEDBACK_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "requests_log.jsonl"


def _rejected_path() -> Path:
    override = os.environ.get("MERCHANT_FEEDBACK_REJECTIONS_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "suggestions_rejected.json"


# ── Text normalisation for mining ─────────────────────────────────────────
# Identifiers (MX codes, TIDs, phones, emails, account numbers, 2ISW ids,
# BVNs, payables, aliases, ACCT/STATIC numbers) — they belong to the merchant,
# never to the request language.
_IDENTIFIER_RE = re.compile(
    r"\b(?:MX\d{4,8}\b|\d{4}[A-Z]\d{3}\b|(?:[+]?234|0)[789]\d{9}\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\d{6,14}\b|"
    r"2ISW[A-Z0-9]{4,}\b|ACCT\d+\b|STATIC\d+\b|MID\d+\b|BVN\d+\b)",
    re.IGNORECASE,
)

# Filler/instruction words stripped from text before n-gram mining. These are
# request-language, never intent-language — they can never form a useful pattern.
_MINING_STOP = {
    "a", "an", "and", "any", "all", "are", "about", "above", "also",
    "below", "can", "could", "do", "does", "did", "for", "from", "get",
    "give", "given", "help", "i", "in", "into", "is", "it", "its",
    "kindly", "me", "my", "need", "of", "on", "or", "out", "please",
    "pls", "plz", "provide", "show", "some", "than", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "to",
    "use", "used", "want", "we", "what", "which", "with", "would",
    "you", "your", "thanks", "thank", "dear", "hello", "hi", "good",
    "morning", "afternoon", "evening", "per", "as", "see", "below",
    "following", "respectively", "one", "two", "three", "first", "last",
    "next", "both", "step", "more", "too", "also", "plus", "via",
    "using", "upon", "under", "over", "here", "there", "now", "not",
    "no", "yes", "assist", "assistance", "check", "checking", "tied",
    "tie", "tied", "mapped", "map", "mapping", "request", "requested",
    "query", "list", "file", "files", "sheet", "sheets", "data",
    "database", "record", "records", "entry", "entries", "info",
    "information", "details", "detail", "above", "below", "the",
    "this", "that", "these", "those", "have", "has", "had", "been",
    "being", "called", "named", "known", "wants", "want", "wanted",
    "needs", "needed", "would", "could", "should", "might", "maybe",
    "perhaps", "let", "us", "know", "also", "too", "like", "just",
    "please", "kindly", "help", "assist", "thank", "thanks", "regards",
    "dear", "hello", "hi", "good", "morning", "afternoon", "evening",
    "note", "noted", "find", "attached", "herewith", "hereby",
    "respectively", "following", "regarding", "re", "fwd", "fw",
}


def _filter_tokens(text: str) -> List[str]:
    """Lowercase, strip identifiers and filler words, return meaningful tokens."""
    low = _IDENTIFIER_RE.sub(" ", (text or "").lower())
    return [w for w in low.split()
            if len(w) >= 3 and w.isalpha() and w not in _MINING_STOP]


def _ngrams(tokens: List[str]) -> List[str]:
    """1, 2, and 3-grams from the filtered token list."""
    out: set = set()
    for w in tokens:
        out.add(w)
    for i in range(len(tokens) - 1):
        out.add(tokens[i] + " " + tokens[i + 1])
    for i in range(len(tokens) - 2):
        out.add(tokens[i] + " " + tokens[i + 1] + " " + tokens[i + 2])
    return sorted(out, key=lambda x: (-len(x.split()), x))


def _is_intent_keyword(w: str) -> bool:
    """True when `w` appears as a keyword or inside a keyword phrase for any
    registered intent — single-word nouns that are too generic to suggest."""
    from .tasks import vocab
    for kws in vocab.INTENT_KEYWORDS.values():
        for kw in kws:
            if w in kw.lower().split():
                return True
    return False


# ── Coverage check (skip n-grams already covered by existing patterns) ────
def _covered(words: List[str], intent_spec: Dict[str, Any]) -> bool:
    """True when every word in the n-gram is already covered by the intent's
    current keywords OR pattern strings — the suggestion would be redundant."""
    pats = [str(p.get("pattern", "")) for p in (intent_spec.get("patterns") or [])]
    kws = [str(k).lower() for k in (intent_spec.get("keywords") or [])]
    for kw in kws:
        kwt = kw.split()
        if all(w in kwt for w in words):
            return True
    for p in pats:
        low_p = p.lower()
        if all(re.search(rf"\b{re.escape(w)}\b", low_p) for w in words):
            return True
    return False


# ── Request log ───────────────────────────────────────────────────────────
def _read() -> List[Dict[str, Any]]:
    """All log entries, oldest first. Corrupt lines skipped."""
    path = _log_path()
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return out


def _write(entries: List[Dict[str, Any]]) -> None:
    """Overwrite the log with the given entries."""
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _next_id(entries: List[Dict[str, Any]]) -> int:
    return max((e.get("id") or 0 for e in entries), default=0) + 1


def entity_signature(task: Dict[str, Any]) -> List[str]:
    """Merchant identifiers + names from a task descriptor used to detect
    rephrase relationships (same merchant, different wording)."""
    ids = task.get("identifiers") or {}
    sig: List[str] = []
    for kind, vals in ids.items():
        for v in vals:
            sig.append(str(v).upper().strip())
    for n in task.get("names") or []:
        sig.append(str(n).upper().strip())
    return sorted(s.strip() for s in sig if s.strip())


def log_request(kind: str, text: str, intent: str, *,
                intents: Optional[List[str]] = None,
                confidence: int = 0,
                identifier_count: int = 0,
                rows: int = 0,
                not_found: int = 0,
                entity_sig: Optional[List[str]] = None) -> None:
    """Persist a request to the log and detect rephrase relationships.

    When a new request with entity overlap follows an empty-result request
    within REPHRASE_WINDOW seconds, the earlier request is tagged as
    'rephrased' with the new request's intent as the correction target.
    """
    text = (text or "").strip()
    if not text:
        return
    entry = {
        "id": 0,  # set below
        "ts": time.time(),
        "kind": kind,
        "text": text[:300],
        "intent": intent or "",
        "intents": list(intents) if intents else [],
        "confidence": int(confidence),
        "identifier_count": int(identifier_count),
        "rows": int(rows),
        "not_found": int(not_found),
        "entity_sig": [s for s in (entity_sig or []) if s],
        "outcome": None,  # set by rephrase detection
        "corrected_to": None,
        "rephrased_by": None,
    }
    with _lock:
        entries = _read()
        now = entry["ts"]
        eid = _next_id(entries)
        entry["id"] = eid
        # ── Rephrase detection ──
        # A PREVIOUS request with empty results, within the window, sharing
        # an entity with the NEW request, is tagged as "rephrased" — the user
        # re-asked the same merchant with different wording.
        new_sig = set(entry["entity_sig"])
        if new_sig and entry["kind"] == "task":
            for prev in entries:
                if prev.get("outcome"):
                    continue
                if int(prev.get("rows") or 0) > 0:
                    continue  # prev found results — not a failed request
                if now - float(prev.get("ts", 0)) > REPHRASE_WINDOW:
                    continue
                prev_sig = set(prev.get("entity_sig") or [])
                if not (new_sig & prev_sig):
                    continue
                # Same text verbatim is a retry, not a rephrase
                if (prev.get("text") or "").strip().lower() == text.lower():
                    continue
                prev["outcome"] = "rephrased"
                prev["corrected_to"] = entry["intent"]
                prev["rephrased_by"] = eid
        entries.append(entry)
        # Prune
        if len(entries) > MAX_LOG_ENTRIES:
            entries = entries[-MAX_LOG_ENTRIES:]
        # Re-assign IDs after pruning (keep monotonic)
        for i, e in enumerate(entries):
            e["id"] = i + 1
            if e.get("rephrased_by") and e["rephrased_by"] > MAX_LOG_ENTRIES:
                e["rephrased_by"] = e["id"] + 1  # approximate; fine for stats
        _write(entries)


def load() -> List[Dict[str, Any]]:
    """Public read of the request log, oldest first."""
    return _read()


# ── Outcome derivation (lazy: computed on read, not stored) ──────────────
def _outcome(e: Dict[str, Any], now: float) -> str:
    if e.get("outcome"):
        return e["outcome"]
    rows = int(e.get("rows") or 0)
    if rows > 0:
        return "accepted"
    if now - float(e.get("ts", 0)) > REPHRASE_WINDOW:
        return "abandoned"
    return "pending"


# ── Pattern mining ────────────────────────────────────────────────────────
def _mine_from(sources: List[Tuple[str, str, str]],
               rejected: Dict[str, Any],
               intents_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Core mining: (text, corrected_intent, label) -> suggestions."""
    from .tasks import vocab
    valid = {i for i in vocab.INTENT_KEYWORDS if i in intents_cfg}
    counts: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for text, intent, label in sources:
        intent = intent.strip().lower()
        if intent not in valid:
            continue
        toks = _filter_tokens(text)
        if not toks:
            continue
        for ngram in _ngrams(toks):
            if len(ngram) < 3:
                continue  # skip single-letter remains
            key = (ngram, intent)
            bucket = counts.setdefault(key, {
                "n": 0, "examples": [], "labels": set(),
            })
            bucket["n"] += 1
            if len(bucket["examples"]) < 3 and text not in bucket["examples"]:
                bucket["examples"].append(text[:160])
            bucket["labels"].add(label)

    raw: List[Dict[str, Any]] = []
    for (ngram, intent), bucket in counts.items():
        if bucket["n"] < MIN_PATTERN_SAMPLES:
            continue
        key = f"{ngram}::{intent}"
        if key in rejected:
            continue
        words = ngram.split()
        spec = intents_cfg.get(intent, {})
        if _covered(words, spec):
            continue
        # Single-word n-grams that are an intent keyword are too generic
        if len(words) == 1 and _is_intent_keyword(words[0]):
            continue
        weight = min(MAX_SUGGESTED_WEIGHT,
                     MIN_SUGGESTED_WEIGHT + (bucket["n"] - MIN_PATTERN_SAMPLES))
        raw.append({
            "ngram": ngram,
            "intent": intent,
            "samples": bucket["n"],
            "weight": weight,
            "examples": bucket["examples"],
            "labels": sorted(bucket["labels"]),
        })

    # Dedupe: if a shorter ngram is a substring of a longer one for the same
    # intent and the longer has at least as many samples, drop the shorter.
    # Sorted by word count desc so longer ngrams are considered first.
    raw.sort(key=lambda s: (-len(s["ngram"].split()), -s["samples"]))
    deduped: List[Dict[str, Any]] = []
    for s in raw:
        words = s["ngram"].split()
        keep = True
        for d in deduped:
            if d["intent"] != s["intent"]:
                continue
            d_words = d["ngram"].split()
            # Check if s's ngram is a contiguous substring of d's ngram
            # (by word sequence, not character)
            if len(words) < len(d_words):
                for i in range(len(d_words) - len(words) + 1):
                    if d_words[i:i + len(words)] == words:
                        keep = False
                        break
            if not keep:
                break
        if keep:
            deduped.append(s)

    deduped.sort(key=lambda s: (-s["samples"], -s["weight"]))
    return deduped[:MAX_SUGGESTIONS]


def mine_patterns() -> List[Dict[str, Any]]:
    """Public mining: reads calibration overrides + request log rephrased
    entries, returns deduplicated, ranked suggestions."""
    from . import calibration
    from .tasks import vocab

    sources: List[Tuple[str, str, str]] = []

    # 1) Clarification overrides from the calibration log
    for e in calibration.load():
        if e.get("source") == "override" and e.get("chosen") and e.get("text"):
            sources.append((e["text"], e["chosen"], "clarification override"))

    # 2) Rephrased task requests from the request log
    for e in _read():
        if (e.get("outcome") == "rephrased"
                and e.get("kind") == "task"
                and e.get("corrected_to")):
            sources.append((e["text"], e["corrected_to"], "rephrased request"))

    rejected = _rejected()
    intents_cfg = vocab.get_intent_config().get("intents") or {}
    return _mine_from(sources, rejected, intents_cfg)


# ── Rejections ────────────────────────────────────────────────────────────
def _rejected() -> Dict[str, Any]:
    """Read rejected suggestions (file may be missing or corrupt)."""
    path = _rejected_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def reject(ngram: str, intent: str) -> None:
    """Record a rejected suggestion so it never resurfaces."""
    ngram = (ngram or "").strip().lower()
    intent = (intent or "").strip().lower()
    if not ngram or not intent:
        return
    key = f"{ngram}::{intent}"
    data = _rejected()
    data[key] = {"ts": time.time(), "ngram": ngram, "intent": intent}
    path = _rejected_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        except OSError as exc:
            logger.warning("failed to write rejected suggestion: %s", exc)


# ── Apply a suggestion to intents.json ────────────────────────────────────
def apply_pattern(ngram: str, intent: str, weight: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Write the mined pattern to intents.json and hot-reload.

    Adds:
      - A regex pattern: \\bword1\\s+word2\\b (weight)
      - The n-gram phrase to the intent's keywords list.

    Returns the new intent spec on success, None on failure.
    """
    from .tasks import vocab
    ngram = (ngram or "").strip().lower()
    intent = (intent or "").strip().lower()
    if not ngram or not intent:
        return None
    words = ngram.split()
    if not words:
        return None
    cfg = vocab.get_intent_config().get("intents") or {}
    spec = cfg.get(intent)
    if spec is None:
        return None
    pattern = r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"
    weight = max(1, min(10, weight or MIN_SUGGESTED_WEIGHT))
    pats = [dict(p) for p in (spec.get("patterns") or [])]
    if not any(p.get("pattern") == pattern for p in pats):
        pats.append({"pattern": pattern, "weight": weight})
    kws = [str(k) for k in (spec.get("keywords") or [])]
    if ngram not in kws:
        kws.append(ngram)
    # Preserve all other fields (label, name_capable, requires, produces, etc.)
    new_spec = dict(spec)
    new_spec["patterns"] = pats
    new_spec["keywords"] = kws
    try:
        vocab.save_intent_config(intent, new_spec)
    except OSError as exc:
        logger.warning("failed to save pattern suggestion: %s", exc)
        return None
    # Lockstep: the config file is only one half of the story — the [4h]
    # parity test in tests/test_tasks.py asserts INTENT_PATTERNS ==
    # _DEFAULT_INTENT_PATTERNS, and vocab.py's defaults must track the
    # config or the suite (and any fresh-clone parity check) drifts. The
    # Tier-1 enrichment module owns this regeneration (design doc §4).
    try:
        from .tasks import enrichment
        enrichment.regenerate_vocab_defaults()
    except Exception as exc:  # noqa: BLE001 — never fail the accept on sync
        logger.warning("vocab.py lockstep regeneration failed: %s", exc)
    # Phase B (design doc §5): one curated approval, two consumers. The
    # approved n-gram becomes a Tier-2 exemplar too, so the embedding tier
    # learns the same phrase the regex tier just learned. Idempotent — a
    # duplicate is a no-op, never a write.
    try:
        from .tasks import enrichment
        enrichment.append_exemplar(intent, ngram)
    except Exception as exc:  # noqa: BLE001 — never fail the accept on sync
        logger.warning("exemplar append failed: %s", exc)
    return new_spec


# ── Stats ─────────────────────────────────────────────────────────────────
def report() -> Dict[str, Any]:
    """Public status: suggestions + outcome stats for the UI."""
    from . import calibration
    entries = _read()
    now = time.time()
    task_count = sum(1 for e in entries if e.get("kind") == "task")
    search_count = sum(1 for e in entries if e.get("kind") == "search")
    outcomes: Dict[str, int] = {}
    for e in entries:
        oc = _outcome(e, now)
        outcomes[oc] = outcomes.get(oc, 0) + 1
    ovr = [e for e in calibration.load() if e.get("source") == "override"]
    outcomes["overridden"] = len(ovr)
    suggestions = mine_patterns()
    return {
        "suggestions": suggestions,
        "stats": {
            "logged": len(entries),
            "tasks": task_count,
            "searches": search_count,
            "accepted": outcomes.get("accepted", 0),
            "abandoned": outcomes.get("abandoned", 0),
            "pending": outcomes.get("pending", 0),
            "rephrased": outcomes.get("rephrased", 0),
            "overridden": len(ovr),
        },
    }