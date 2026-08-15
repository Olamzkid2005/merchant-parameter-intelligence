"""
semantic.py — Tier 2: local embedding semantic match (Phase 1: shadow mode).

The second tier of the hybrid semantic intent layer (docs/hybrid-semantic-
intent-layer.md). When Tier 1 (regex patterns + the existing ~semantic keyword
fallback) is inconclusive — the request is about to hit the clarification
card — this module decides which intent the query's MEANING points at, using
local embeddings only. No LLM, no network, no merchant data leaves the
machine.

Design (mirrors the doc's §3/§5/§6/§9/§11):

  - resolve(query, task) -> a calibrated 0-100 decision, or None when the
    tier is unusable. Callers (engine.suggest_clarification) decide whether
    to shadow-log it (Phase 1, mode="shadow") or act on it (Phase 2,
    mode="enabled").
  - Exemplars: per-intent phrase sets. data/exemplars.json when present
    (produced by scripts/build_exemplars.py); otherwise the Phase-A cold
    start is derived at runtime from the live vocab's keyword lists — the
    same curated phrases the ~semantic tier already uses, so the cold start
    needs no WordNet, no network and no extra files.
  - Encoders: HashingEncoder is the deterministic pure-Python fallback (word
    + character n-gram hashing, L2-normalised, cosine) so shadow mode runs on
    any machine with zero extra dependencies. The production path is an ONNX
    export of a small sentence-transformer (all-MiniLM-L6-v2 / bge-small);
    ONNXEncoder slots into the same `encode()` interface once
    scripts/export_embedding_model.py ships the artifact. The encoder is a
    config value, never hardcoded into resolve().
  - Calibration (§6): cosine lives in a tight band; a piecewise-linear map
    (COS_FLOOR..COS_CEIL -> 0..100) puts Tier 2 on the same 0-100 scale as
    Tier 1 so the same threshold/margin gates apply. The mapping is a Phase-1
    default; Phase 3 re-fits it from the shadow log inside calibration.py.
  - Shadow log: data/tier2_shadow.jsonl (append-only, gitignored, overridable
    via MERCHANT_TIER2_SHADOW_FILE) — one line per Tier 2 decision, correlated
    with the calibration log by text for the Phase 1 precision measurement.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from .vocab import INTENT_KEYWORDS, _lower

logger = logging.getLogger(__name__)

# ── Phase-1 tuning (Phase 3 folds these into calibration.py) ──────────────
SEMANTIC_THRESHOLD = 65   # calibrated 0-100; at/above this the tier WOULD act
SEMANTIC_MARGIN = 15      # calibrated-point gap required over the runner-up intent
COS_FLOOR = 0.30          # below this cosine the tier never acts (noise floor)
_CAL_A, _CAL_B = 0.30, 0.85   # piecewise-linear calibration bounds (cosine)

_ENCODER_ID = "hash-ngram-v1"
_DIM = 1024

# Identifier shapes masked before embedding (mirrors feedback._IDENTIFIER_RE:
# TIDs, MX codes, phones, emails, account/BVN/MID numbers — they belong to the
# merchant, never to the request's intent language).
_IDENTIFIER_RE = re.compile(
    r"\b(?:MX\d{4,8}\b|\d{4}[A-Z]\d{3}\b|(?:[+]?234|0)[789]\d{9}\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\d{6,14}\b|"
    r"2ISW[A-Z0-9]{4,}\b|ACCT\d+\b|STATIC\d+\b|MID\d+\b|BVN\d+\b)",
    re.IGNORECASE,
)

# Function-word stoplist for the hashing tokenizer — request scaffolding, not
# intent language. Intent-neutral and small on purpose: merchant tokens are
# masked to [MERCHANT] before this runs, so nothing domain-specific is lost.
_STOP = {
    "a", "an", "and", "any", "are", "as", "at", "be", "but", "by", "can",
    "could", "do", "does", "did", "for", "from", "get", "give", "has",
    "have", "how", "i", "in", "into", "is", "it", "its", "list", "me",
    "my", "of", "on", "or", "please", "pls", "plz", "show", "that", "the",
    "their", "them", "there", "these", "they", "this", "those", "to",
    "use", "want", "we", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your",
}

_lock = threading.Lock()


# ── Calibration (§6): cosine band -> the same 0-100 scale as Tier 1 ───────
def _calibrate(cos: float) -> int:
    """Piecewise-linear cosine -> calibrated confidence (0-100)."""
    if cos <= _CAL_A:
        return 0
    if cos >= _CAL_B:
        return 100
    return int(round((cos - _CAL_A) / (_CAL_B - _CAL_A) * 100))


# ── Hashing encoder (pure Python, zero deps) ──────────────────────────────
def _hash_str(s: str) -> int:
    """Stable hash (md5-based — Python's hash() is per-process randomized)."""
    return int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:8], "big")


class HashingEncoder:
    """Deterministic bag-of-hashed-features encoder: word + char n-grams.

    Weaker than a transformer — that is the ONNX path's job — but fully
    offline, stable across processes, and fast enough to run per-request,
    which is exactly what shadow mode needs: real Tier-2 decisions to log and
    calibrate against before the production encoder ships. Vectors are sparse
    L2-normalised dicts (no numpy).
    """

    id = _ENCODER_ID

    def encode(self, texts: List[str]) -> List[Dict[int, float]]:
        return [self._vec(t) for t in texts]

    def _tokens(self, text: str) -> List[str]:
        words = re.findall(r"[a-z0-9]+", _lower(text))
        return [w for w in words if len(w) >= 2 and w not in _STOP]

    def _features(self, tok: str) -> List[str]:
        feats = [f"w:{tok}"]
        if len(tok) >= 3:
            for n in (2, 3, 4):
                for i in range(len(tok) - n + 1):
                    feats.append(f"c{n}:" + tok[i:i + n])
        return feats

    def _vec(self, text: str) -> Dict[int, float]:
        vec: Dict[int, float] = {}
        for tok in self._tokens(text):
            for feat in self._features(tok):
                h = _hash_str(feat)
                dim = h % _DIM
                sign = 1.0 if (h >> 8) & 1 else -1.0
                vec[dim] = vec.get(dim, 0.0) + sign
        norm = sum(v * v for v in vec.values()) ** 0.5
        if norm:
            for k in vec:
                vec[k] /= norm
        return vec


class ONNXEncoder:
    """Production encoder — loads an exported sentence-transformer model.

    Not implemented yet: the ONNX artifact + tokenizer come from
    scripts/export_embedding_model.py (Phase 0 deliverable, pending the
    onnxruntime/nltk dependency install). Until the model exists, the module
    uses HashingEncoder; this class documents the exact seam a real encoder
    must fill (same `encode()` -> list of vectors contract).
    """

    id = "onnx-minilm-v1"

    def encode(self, texts: List[str]) -> List[Any]:
        raise NotImplementedError(
            "ONNX encoder needs the exported model (scripts/export_embedding_model.py); "
            "HashingEncoder is the active fallback.")


def _make_encoder():
    """Active encoder — the ONNX path once the model artifact exists."""
    return HashingEncoder()


# ── Exemplars ─────────────────────────────────────────────────────────────
def _exemplar_file() -> Path:
    return config.DATA_DIR / "exemplars.json"


def load_exemplars() -> Dict[str, List[str]]:
    """Per-intent exemplar phrases, Phase A.

    data/exemplars.json ({"intents": {"<intent>": ["phrase", ...]}}) when
    present — the auditable output of scripts/build_exemplars.py. Absent
    (cold start, no build tooling yet): derive from the live vocab keyword
    lists, the same curated phrases the ~semantic tier already uses.
    """
    try:
        data = json.loads(_exemplar_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("intents"), dict):
            out = {i: [str(p) for p in ph]
                   for i, ph in data["intents"].items() if ph}
            if out:
                return out
    except Exception:
        pass
    return {i: list(ph) for i, ph in INTENT_KEYWORDS.items() if ph}


def _fingerprint(exemplars: Dict[str, List[str]]) -> str:
    return hashlib.md5(
        json.dumps(exemplars, sort_keys=True).encode("utf-8")).hexdigest()[:12]


# ── Query masking (§11): intent language only, no clause/entity noise ─────
def mask_query(text: str, task: Optional[Dict[str, Any]] = None) -> str:
    """Replace recognized spans with placeholder tokens before embedding.

    Runs the existing identifier/merchant resolution data first (the task's
    parsed names + identifiers), then a regex sweep for identifier shapes the
    parser may have missed — so the vector stays focused on intent language
    regardless of the surrounding clause structure.
    """
    t = (text or "").strip()
    if task:
        # Case-insensitive word-boundary replace: the parsed names are
        # canonical (uppercase) while the raw query may be lowercase.
        for n in task.get("names") or []:
            if n:
                t = re.sub(r"\b" + re.escape(str(n)) + r"\b", " [MERCHANT] ",
                           t, flags=re.IGNORECASE)
        for kind, vals in (task.get("identifiers") or {}).items():
            for v in vals:
                if v:
                    t = re.sub(r"\b" + re.escape(str(v)) + r"\b",
                               f" [{kind.upper()}] ", t, flags=re.IGNORECASE)
    t = _IDENTIFIER_RE.sub(" [ID] ", t)
    return " ".join(t.split())


# ── Scoring ───────────────────────────────────────────────────────────────
def _cosine(a: Dict[int, float], b: Dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    for k, v in a.items():
        w = b.get(k)
        if w:
            dot += v * w
    return dot


@lru_cache(maxsize=256)
def _rank_cached(masked: str, fingerprint: str, encoder_id: str
                 ) -> Optional[Dict[str, Any]]:
    """Rank the masked query against every intent's exemplars (cached).

    Cache key = masked text + exemplar fingerprint + encoder id, so a config
    hot-reload or model swap can never serve stale vectors.
    """
    exemplars = load_exemplars()
    if not exemplars:
        return None
    enc = _make_encoder()
    qv = enc.encode([masked])[0]
    per_intent: Dict[str, Tuple[float, str]] = {}
    winner: Optional[Tuple[str, str, float]] = None  # (intent, exemplar, cos)
    for intent, phrases in exemplars.items():
        best_cos, best_ph = 0.0, phrases[0]
        for ph, pv in zip(phrases, enc.encode(phrases)):
            c = _cosine(qv, pv)
            if c > best_cos:
                best_cos, best_ph = c, ph
        per_intent[intent] = (best_cos, best_ph)
        if winner is None or best_cos > winner[2]:
            winner = (intent, best_ph, best_cos)
    if winner is None:
        return None
    w_intent, w_ph, w_cos = winner
    # Runner-up INTENT's best exemplar — the margin gate (§3) is about intent
    # competition, not phrase competition within one intent.
    second: Optional[Tuple[str, float]] = None
    for i, (c, _p) in per_intent.items():
        if i != w_intent and (second is None or c > second[1]):
            second = (i, c)
    second_cos = second[1] if second else 0.0
    conf = _calibrate(w_cos)
    second_conf = _calibrate(second_cos)
    return {
        "intent": w_intent,
        "exemplar": w_ph,
        "cosine": round(w_cos, 4),
        "confidence": conf,
        "second_intent": second[0] if second else None,
        "margin": conf - second_conf,
        "would_act": bool(
            w_cos >= COS_FLOOR and conf >= SEMANTIC_THRESHOLD
            and conf - second_conf >= SEMANTIC_MARGIN),
        "encoder": encoder_id,
    }


def resolve(text: str, task: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Tier-2 semantic decision for a query, or None when the tier is unusable.

    Returns the winning intent with its calibrated confidence, the matched
    exemplar phrase (explainability, §8), the margin over the runner-up
    intent, and `would_act` — whether the decision clears the threshold +
    margin gates. Shadow callers log this via log_shadow(); enabled callers
    act on `would_act` directly.
    """
    masked = mask_query(text, task)
    exemplars = load_exemplars()
    if not exemplars:
        return None
    return _rank_cached(masked, _fingerprint(exemplars), _make_encoder().id)


# ── Shadow log (Phase 1) ──────────────────────────────────────────────────
def _shadow_path() -> Path:
    override = os.environ.get("MERCHANT_TIER2_SHADOW_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "tier2_shadow.jsonl"


def log_shadow(entry: Dict[str, Any]) -> None:
    """Append one Tier-2 shadow decision (gitignored data/, append-only).

    Entries correlate with the calibration log by `text` so Phase 1 can
    measure precision: a request whose Tier-2 pick matches the intent the
    user later confirms is a true positive, otherwise a false positive.
    """
    if not (entry or {}).get("text"):
        return
    line = json.dumps(entry, default=str)
    with _lock:
        path = _shadow_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("tier2 shadow log write failed: %s", exc)


def read_shadow() -> List[Dict[str, Any]]:
    """All shadow entries, oldest first (corrupt lines skipped)."""
    path = _shadow_path()
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
