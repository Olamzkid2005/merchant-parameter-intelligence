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
    export of a small sentence-transformer (all-MiniLM-L6-v2 / bge-small):
    ONNXEncoder loads the artifact produced by scripts/export_embedding_model.py
    and falls back to hashing when the model or onnxruntime is missing. The
    encoder is a config value (MERCHANT_TIER2_ENCODER), never hardcoded into
    resolve().
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
from .intents import _detect_negated_intents
from .vocab import INTENT_KEYWORDS, _lower

logger = logging.getLogger(__name__)

# ── Phase-1 tuning (Phase 3 folds these into calibration.py) ──────────────
SEMANTIC_THRESHOLD = 65   # calibrated 0-100; at/above this the tier WOULD act
SEMANTIC_MARGIN = 15      # calibrated-point gap required over the runner-up intent
COS_FLOOR = 0.30          # below this cosine the tier never acts (noise floor)
_CAL_A, _CAL_B = 0.30, 0.85   # piecewise-linear calibration bounds (cosine)

_ENCODER_ID = "hash-ngram-v1"
_DIM = 1024

# Production encoder (design doc §9) — override via MERCHANT_TIER2_MODEL.
_MODEL_ID = os.environ.get("MERCHANT_TIER2_MODEL", "all-MiniLM-L6-v2")

# Identifier shapes masked before embedding (mirrors feedback._IDENTIFIER_RE:
# TIDs, MX codes, phones, emails, account/BVN/MID numbers — they belong to the
# merchant, never to the request's intent language).
_IDENTIFIER_RE = re.compile(
    r"\b(?:MX\d{4,8}\b|\d{4}[A-Z]\d{3}\b|(?:[+]?234|0)[789]\d{9}\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\d{6,14}\b|"
    r"2ISW[A-Z0-9]{4,}\b|ACCT\d+\b|STATIC\d+\b|MID\d+\b|BVN\d+\b)",
    re.IGNORECASE,
)

# mask_query placeholders: [MERCHANT], [MX], [TID], [ID], ... — identifiers
# and merchant names carry no intent signal, so both encoders drop them
# before embedding (kept as bracketed tokens in mask_query's public output).
_STRIP_PLACEHOLDERS = re.compile(r"\[[A-Z_]+\]")

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
        # mask_query placeholders ([MERCHANT], [MX], [ID]) are artifacts of
        # identifier/name masking, not intent language — drop them so a
        # merchant name can never leak a word (e.g. "merchant") into the
        # vector.
        text = _STRIP_PLACEHOLDERS.sub(" ", text)
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
    """Production encoder — ONNX export of a small sentence-transformer
    (Phase-0 default all-MiniLM-L6-v2, design doc §9).

    Loads the artifact produced by scripts/export_embedding_model.py
    (data/models/<model_id>/model.onnx + tokenizer.json) and runs it through
    onnxruntime + the fast tokenizers library, mean-pooling the last hidden
    state and L2-normalising — the standard sentence-transformer pooling, so
    it fills the same `encode()` -> list of vectors contract as
    HashingEncoder. _make_encoder() falls back to hashing when the artifact
    or onnxruntime is unavailable, so the tier degrades gracefully on any
    machine without the model.
    """

    def __init__(self, model_id: str = _MODEL_ID):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        d = config.DATA_DIR / "models" / model_id
        self.model_id = model_id
        self.id = f"onnx-{model_id}"
        self._session = ort.InferenceSession(
            str(d / "model.onnx"), providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        tok.enable_truncation(max_length=256)
        # Dynamic padding (no fixed length) — the batch pads to its own
        # longest sentence, not to 256, so short exemplars stay cheap.
        tok.enable_padding()
        self._tok = tok

    def encode(self, texts: List[str]) -> List[Any]:
        import numpy as np
        # Drop mask_query placeholder tokens ([MERCHANT], [MX], [ID]) so the
        # embedding only ever sees true intent language (see HashingEncoder).
        texts = [_STRIP_PLACEHOLDERS.sub(" ", t) for t in texts]
        encs = self._tok.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encs], dtype=np.int64)
        attn = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attn}
        # sentence-transformers ONNX exports also take token_type_ids.
        if hasattr(encs[0], "type_ids") and encs[0].type_ids:
            feeds["token_type_ids"] = np.array(
                [e.type_ids for e in encs], dtype=np.int64)
        last_hidden = self._session.run(None, feeds)[0]
        mask = attn[:, :, None].astype(np.float32)
        vecs = (last_hidden * mask).sum(axis=1) / np.clip(
            mask.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return [v / n for v, n in zip(vecs, np.clip(norms, 1e-9, None))]


# Active encoder memoised per selection choice so the ONNX session is loaded
# once per process, not per request (resolve() is lru_cached, but the cache
# key needs the encoder id on every call). Tests flip MERCHANT_TIER2_ENCODER
# between runs; each choice keeps its own memoised instance.
_encoder_cache: Dict[str, Any] = {}
_encoder_lock = threading.Lock()


def _build_encoder(choice: str):
    if choice == "hash":
        return HashingEncoder()
    try:
        return ONNXEncoder()
    except Exception as exc:
        if choice == "onnx":
            logger.warning(
                "ONNX encoder unavailable (%s) — falling back to hashing", exc)
        return HashingEncoder()


def _make_encoder():
    """Active encoder — env-selectable, ONNX when the artifact exists.

    MERCHANT_TIER2_ENCODER=hash|onnx|auto (default auto): auto uses the ONNX
    encoder when data/models/<model> + onnxruntime are present, else the
    deterministic pure-Python HashingEncoder.
    """
    choice = os.environ.get("MERCHANT_TIER2_ENCODER", "auto") or "auto"
    cached = _encoder_cache.get(choice)
    if cached is not None:
        return cached
    with _encoder_lock:
        cached = _encoder_cache.get(choice)
        if cached is None:
            cached = _build_encoder(choice)
            _encoder_cache[choice] = cached
    return cached


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
def _cosine(a, b) -> float:
    """Cosine between two vectors — dense ndarray (ONNX) or sparse dict
    (hashing). Vectors are L2-normalised by their encoders, so this is a
    dot product."""
    if a is None or b is None:
        return 0.0
    if hasattr(a, "ndim"):
        import numpy as np
        return float(np.dot(a, b))
    if not a or not b:
        return 0.0
    dot = 0.0
    for k, v in a.items():
        w = b.get(k)
        if w:
            dot += v * w
    return dot


# Precomputed exemplar vectors per (fingerprint, encoder) — encoding ~190
# exemplar phrases is the expensive part of a resolve (seconds for the ONNX
# encoder); a module-level cache makes it a one-time cost per process and
# per config change. Keyed on the exemplar fingerprint + encoder id so a
# hot-reload or model swap rebuilds automatically.
#
# Phase-0 serving requirement (design doc §9): the vectors are ALSO persisted
# to a versioned artifact, data/exemplar_embeddings_<encoder>_<fingerprint>.npy
# (+ a JSON sidecar with the row layout). A restart then cold-starts from
# disk instead of re-encoding ~190 phrases — the version is the exemplar
# fingerprint + encoder id baked into the filename, so a model swap or an
# exemplar edit builds a fresh artifact and never serves stale vectors.
_exemplar_vecs: Dict[Tuple[str, str], Dict[str, List[Tuple[str, Any]]]] = {}
_exemplar_vecs_lock = threading.Lock()


def _exemplar_vecs_path(encoder_id: str, fingerprint: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", encoder_id)
    return config.DATA_DIR / f"exemplar_embeddings_{safe}_{fingerprint}.npy"


def _to_dense(v, dim: int):
    """Vector -> dense numpy row (dict form for the hashing encoder, ndarray
    for the ONNX encoder) so every encoder persists through the same .npy."""
    import numpy as np
    if hasattr(v, "ndim"):
        return np.asarray(v, dtype=np.float32)
    arr = np.zeros(dim, dtype=np.float32)
    for k, val in v.items():
        arr[int(k) % dim] = val
    return arr


def _from_stored(v, enc):
    """Dense row back into the encoder's native form (sparse dict for the
    hashing encoder, ndarray for ONNX) so _cosine always sees a consistent
    representation."""
    if getattr(enc, "model_id", None) is not None:  # ONNX -> keep dense
        return v
    arr = v
    out = {}
    for idx in range(len(arr)):
        val = float(arr[idx])
        if val:
            out[idx] = val
    return out


def _save_exemplar_vecs(key, vecs) -> None:
    """Persist precomputed vectors to the versioned .npy artifact (+ sidecar).
    Best-effort: an unwritable dir or missing numpy must never break resolve."""
    try:
        import numpy as np
        rows = [(intent, ph, v)
                for intent, pairs in vecs.items() for ph, v in pairs]
        if not rows:
            return
        dim = len(rows[0][2]) if hasattr(rows[0][2], "ndim") else 1024
        dense = np.stack([_to_dense(v, dim) for _, _, v in rows])
        sidecar = {
            "encoder": key[1],
            "fingerprint": key[0],
            "layout": [{"intent": i, "phrase": p} for i, p, _ in rows],
        }
        path = _exemplar_vecs_path(key[1], key[0])
        if path.exists():
            return  # already persisted this version
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(str(path), dense)
        path.with_suffix(".json").write_text(
            json.dumps(sidecar), encoding="utf-8")
    except Exception as exc:
        logger.debug("exemplar embedding persistence skipped: %s", exc)


def _load_exemplar_vecs(key, exemplars, enc):
    """Read the versioned artifact for (encoder, fingerprint), or None when
    missing/stale/corrupt — then the caller recomputes and re-persists."""
    try:
        import numpy as np
        path = _exemplar_vecs_path(key[1], key[0])
        sidecar_path = path.with_suffix(".json")
        if not (path.exists() and sidecar_path.exists()):
            return None
        side = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if side.get("encoder") != key[1] or side.get("fingerprint") != key[0]:
            return None
        dense = np.load(str(path))
        layout = side["layout"]
        if dense.shape[0] != len(layout):
            return None
        # The artifact must cover EVERY exemplar phrase — anything less means
        # it predates an exemplar edit and is stale despite the fingerprint.
        need = {(i, p) for i, ph in exemplars.items() for p in ph}
        have = {(m["intent"], m["phrase"]) for m in layout}
        if need != have:
            return None
        out: Dict[str, List[Tuple[str, Any]]] = {}
        for meta, vec in zip(layout, dense):
            out.setdefault(meta["intent"], []).append(
                (meta["phrase"], _from_stored(vec, enc)))
        return out
    except Exception as exc:
        logger.debug("exemplar embedding load skipped: %s", exc)
        return None


def _get_exemplar_vecs(exemplars: Dict[str, List[str]], enc) \
        -> Dict[str, List[Tuple[str, Any]]]:
    key = (_fingerprint(exemplars), enc.id)
    cached = _exemplar_vecs.get(key)
    if cached is not None:
        return cached
    with _exemplar_vecs_lock:
        cached = _exemplar_vecs.get(key)
        if cached is None:
            cached = _load_exemplar_vecs(key, exemplars, enc)
            if cached is None:
                cached = {
                    intent: list(zip(phrases, enc.encode(phrases)))
                    for intent, phrases in exemplars.items()
                }
                _save_exemplar_vecs(key, cached)
            _exemplar_vecs[key] = cached
    return cached


# ── Phase-3 gates (design doc §7): fitted per-intent thresholds ──────────
def _gate_fingerprint() -> str:
    """State of the Tier-2 fit inputs (the review labels), so the resolve
    lru cache evicts when a label moves a fitted gate. Keyed on the review
    file only — new UNLABELED shadow entries can never move a gate, so they
    must not invalidate cached results either."""
    try:
        st = _review_path().stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


def _gate_for(intent: str) -> Tuple[int, int]:
    """Per-request Tier-2 gates (threshold, margin) for a winning intent.

    Phase 3: calibration.fit_tier2() folds the §7 shadow-review labels into
    per-intent thresholds (accept/override evidence on the auto-run band);
    the margin is a single GLOBAL fit — intent competition, not identity.
    Falls back to the Phase-1 constants when an intent has no fitted gate
    or the fit is unavailable."""
    threshold, margin = SEMANTIC_THRESHOLD, SEMANTIC_MARGIN
    try:
        from .. import calibration
        f = calibration.fit_tier2()
        per = (f.get("per_intent") or {}).get(intent)
        if per and per.get("threshold") is not None:
            threshold = int(per["threshold"])
        if f.get("margin") is not None:
            margin = int(f["margin"])
    except Exception as exc:
        logger.warning("tier2 gate lookup failed: %s", exc)
    return threshold, margin


@lru_cache(maxsize=256)
def _rank_cached(masked: str, fingerprint: str, encoder_id: str,
                 gate_fp: str,
                 negated_fp: str = "") -> Optional[Dict[str, Any]]:
    """Rank the masked query against every intent's exemplars (cached).

    Cache key = masked text + exemplar fingerprint + encoder id + gate
    fingerprint + negation fingerprint, so a config hot-reload, model swap,
    or a review label that moves a fitted gate can never serve stale vectors
    or stale gates.  Negated intents (detected from the ORIGINAL text before
    masking) are excluded from the winner selection — a user who says
    "...but not the change history" must never get change_details back.
    """
    exemplars = load_exemplars()
    if not exemplars:
        return None
    enc = _make_encoder()
    qv = enc.encode([masked])[0]
    per_intent: Dict[str, Tuple[float, str]] = {}
    # Decode negated intents from the cache-friendly fingerprint string.
    negated = frozenset(negated_fp.split("|")) if negated_fp else frozenset()
    winner: Optional[Tuple[str, str, float]] = None  # (intent, exemplar, cos)
    for intent, phrases in _get_exemplar_vecs(exemplars, enc).items():
        best_cos, best_ph = 0.0, phrases[0][0]
        for ph, pv in phrases:
            c = _cosine(qv, pv)
            if c > best_cos:
                best_cos, best_ph = c, ph
        per_intent[intent] = (best_cos, best_ph)
        # Skip negated intents — they must never win.  We still record their
        # scores in per_intent so the margin gate can reference them (the
        # runner-up might be negated, which is fine — it just lowers the
        # margin the winner needs to clear).
        if intent in negated:
            continue
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
    threshold, margin = _gate_for(w_intent)
    return {
        "intent": w_intent,
        "exemplar": w_ph,
        "cosine": round(w_cos, 4),
        "confidence": conf,
        "second_intent": second[0] if second else None,
        "margin": conf - second_conf,
        "would_act": bool(
            w_cos >= COS_FLOOR and conf >= threshold
            and conf - second_conf >= margin),
        "encoder": encoder_id,
    }


def resolve(text: str, task: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Tier-2 semantic decision for a query, or None when the tier is unusable.

    Returns the winning intent with its calibrated confidence, the matched
    exemplar phrase (explainability, §8), the margin over the runner-up
    intent, and `would_act` — whether the decision clears the threshold +
    margin gates. Shadow callers log this via log_shadow(); enabled callers
    act on `would_act` directly.

    Negation awareness (§11 fix): the original text is scanned for negation
    markers ("but not ...", "except ...", "without ...") before masking.
    Any intent caught by a negation marker is excluded from the winner
    selection, so "account details but not the change history" never picks
    change_details even when the exemplars score it highest.
    """
    masked = mask_query(text, task)
    exemplars = load_exemplars()
    if not exemplars:
        return None
    # Detect negated intents from the ORIGINAL text (before masking strips
    # identifiers/names that the negation window might reference).
    negated = _detect_negated_intents(text or "")
    negated_fp = "|".join(sorted(negated)) if negated else ""
    return _rank_cached(masked, _fingerprint(exemplars), _make_encoder().id,
                        _gate_fingerprint(), negated_fp)


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


# ── Shadow review (Phase 1 spot-check: the auto-run band) ────────────────
# The design doc §7 is explicit: clarification-outcome labels only cover the
# ambiguous band — a Tier-2 decision confident enough to auto-run
# (tier2_would_act) never reaches the clarification picker, so it never gets
# a free label there. Before Phase 2 can be turned on, someone must manually
# spot-check that high-confidence band. This section is that tool: it
# assigns every shadow entry a stable id, lets a reviewer mark each entry
# correct/wrong (the ground-truth label), and aggregates per-intent
# precision on the would-act band so the Phase 2 go/no-go has evidence.
# Labels live in data/tier2_shadow_review.jsonl (gitignored, append-only;
# MERCHANT_TIER2_REVIEW_FILE overrides for tests).


def _review_path() -> Path:
    override = os.environ.get("MERCHANT_TIER2_REVIEW_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "tier2_shadow_review.jsonl"


def entry_id(entry: Dict[str, Any]) -> str:
    """Stable id for a shadow entry (ts + text), so labels survive re-reads
    of the append-only log and never collide."""
    key = f"{entry.get('ts')}::{entry.get('text')}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def read_review() -> Dict[str, Dict[str, Any]]:
    """All review labels keyed by entry id (oldest first; corrupt lines
    skipped — a hand-edited line must never crash the review UI)."""
    path = _review_path()
    if not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(rec, dict) and rec.get("entry_id"):
                    out[rec["entry_id"]] = rec
    except OSError:
        return {}
    return out


def label_entry(entry_id_: str, correct: bool, *,
                note: str = "", intent: str = "") -> Dict[str, Any]:
    """Record a reviewer's verdict on one shadow entry.

    correct=True  the Tier-2 pick was right (or `intent` names the intent
                  the reviewer says it SHOULD have been)
    correct=False the pick was wrong — the reviewer can pass the actual
                  intent via `intent` so precision stats can see the miss.
    Re-labeling an entry overwrites the earlier verdict (latest wins).
    """
    rec = {
        "entry_id": entry_id_,
        "ts": time.time(),
        "correct": bool(correct),
        "intent": (intent or "").strip().lower(),
        "note": (note or "")[:300],
    }
    with _lock:
        path = _review_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = read_review()
            existing[entry_id_] = rec
            # Rewrite in stable order so a re-label never duplicates rows.
            with open(path, "w", encoding="utf-8") as fh:
                for eid in sorted(existing):
                    fh.write(json.dumps(existing[eid]) + "\n")
        except OSError as exc:
            logger.warning("tier2 shadow review write failed: %s", exc)
            return {"ok": False, "reason": str(exc)}
    return {"ok": True, **rec}


def review(band: str = "all", limit: int = 100) -> Dict[str, Any]:
    """Shadow entries joined with their review labels, band-filtered.

    band="all"       every logged decision
    band="would_act" only the high-confidence auto-run band — the §7
                     spot-check target that clarification labels never cover
    band="would_not" everything else (the tier would NOT have acted)

    Returns entries (oldest first, newest labels attached) plus
    review_stats() so the UI can render the panel in one call.
    """
    labels = read_review()
    entries = []
    for e in read_shadow():
        wa = bool(e.get("tier2_would_act"))
        if band == "would_act" and not wa:
            continue
        if band == "would_not" and wa:
            continue
        eid = entry_id(e)
        row = dict(e)
        row["entry_id"] = eid
        row["label"] = labels.get(eid)
        entries.append(row)
    entries.sort(key=lambda r: float(r.get("ts") or 0))
    return {
        "band": band,
        "count": len(entries),
        "labeled": sum(1 for r in entries if r.get("label")),
        "entries": entries[-limit:],
        "stats": review_stats(),
        "file": str(_shadow_path()),
        "labels_file": str(_review_path()),
    }


def review_stats() -> Dict[str, Any]:
    """Precision on the §7 spot-check band — the Phase 2 go/no-go evidence.

    Only LABELED would_act entries count (that is the band with no free
    labels). Per intent: reviewed / correct / precision. `correct` follows
    the reviewer's verdict; when they supplied the actual intent on a miss,
    `missed_as` shows what the tier should have picked.
    """
    labels = read_review()
    if not labels:
        return {"reviewed": 0, "band_total": 0, "per_intent": {}}
    entries = [e for e in read_shadow() if e.get("tier2_would_act")]
    per: Dict[str, Dict[str, Any]] = {}
    reviewed = 0
    for e in entries:
        lbl = labels.get(entry_id(e))
        if not lbl:
            continue
        reviewed += 1
        intent = e.get("tier2_intent") or "?"
        b = per.setdefault(intent, {"reviewed": 0, "correct": 0,
                                    "missed_as": []})
        b["reviewed"] += 1
        if lbl.get("correct"):
            b["correct"] += 1
        elif lbl.get("intent"):
            b["missed_as"].append(lbl["intent"])
    per_intent = {}
    for intent, b in per.items():
        per_intent[intent] = {
            "reviewed": b["reviewed"],
            "correct": b["correct"],
            "precision": round(b["correct"] / b["reviewed"], 3),
            "missed_as": b["missed_as"][-5:],
        }
    total_correct = sum(b["correct"] for b in per.values())
    return {
        "reviewed": reviewed,
        "band_total": len(entries),
        "correct": total_correct,
        "precision": round(total_correct / reviewed, 3) if reviewed else 0.0,
        "per_intent": per_intent,
    }


def shadow_health() -> Dict[str, Any]:
    """Band-independent shadow-log summary for the Rule Engine health chip.

    Lets the user watch the log accumulate without opening the spot-check
    panel: totals, today's entry count (local time), per-band counts,
    labeled count, and the timestamp of the newest decision.
    """
    entries = read_shadow()
    today = time.strftime("%Y-%m-%d")
    today_n = 0
    would_act = 0
    last_ts = 0.0
    for e in entries:
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > last_ts:
            last_ts = ts
        if ts and time.strftime("%Y-%m-%d", time.localtime(ts)) == today:
            today_n += 1
        if e.get("tier2_would_act"):
            would_act += 1
    return {
        "total": len(entries),
        "today": today_n,
        "would_act": would_act,
        "would_not": len(entries) - would_act,
        "reviewed": len(read_review()),
        "last_ts": last_ts,
        "last_ts_human": time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(last_ts)) if last_ts else "",
    }
