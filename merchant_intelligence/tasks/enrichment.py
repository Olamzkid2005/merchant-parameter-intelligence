"""
enrichment.py — Tier 1 build-time enrichment pipeline (WordNet synonyms).

docs/hybrid-semantic-intent-layer.md §4: expand the deterministic regex tier
with human-curated WordNet synonyms instead of relying on embeddings alone.

Flow (three stages, each a public function):

    1. propose_candidates()   WordNet expands the literal phrases already in
                              intents.json patterns (+ the curated exemplars)
                              into candidate phrases. Writes proposals to
                              data/exemplar_candidates.json. NEVER auto-applies —
                              every candidate starts "pending" for human review.
    2. set_status(ids, ...)   The Rule Engine / CLI marks candidates approved
                              or rejected (the curation gate).
    3. apply_approved()       Merges approved candidates into intents.json as
                              weight-2 patterns AND regenerates vocab.py's
                              _DEFAULT_INTENT_PATTERNS in lockstep (so the
                              shipped-config-vs-defaults parity test can never
                              drift), appends the phrases to data/exemplars.json
                              (Tier 2 exemplars), and records provenance in
                              data/auto_pattern_manifest.json.

Why weight 2: confidence = min(100, score * 12) -> a lone auto-synonym scores
24 — below every confidence-gated threshold (CLARIFY_TOP_MAX = 60 and the
>= 40 gates), so it only tips the balance alongside a real pattern. The one
residual risk (identity-keyed is_task branches can flip on a top-intent
change) is named in the design doc §4 and accepted there.

Why the lockstep regeneration is load-bearing: tests/test_tasks.py's [4h]
check asserts INTENT_PATTERNS == _DEFAULT_INTENT_PATTERNS against the shipped
config. feedback.apply_pattern() writes only to intents.json and silently
breaks that parity; regenerate_vocab_defaults() here is the single fix point
and is called by both apply_approved() and feedback.apply_pattern().

All artifacts live in data/ (gitignored — auditable on disk / via the Rule
Engine, not in git history). The WordNet corpus is an optional dependency:
propose_candidates() degrades to a clear "nltk/wordnet missing" result
instead of crashing, so the app keeps working without it.

Usage (CLI wrapper):
    python scripts/enrich_intents.py --propose
    python scripts/enrich_intents.py --status
    python scripts/enrich_intents.py --approve <id> [<id> ...] | --approve-all
    python scripts/enrich_intents.py --reject <id> [<id> ...]
    python scripts/enrich_intents.py --apply [--ids <id> ...]   # approved by default
    python scripts/enrich_intents.py --check                    # parity gate

Test seam: MERCHANT_INTENTS_CONFIG points at a temp intents.json and
MERCHANT_INTENTS_VOCAB at a temp copy of vocab.py, so the suite exercises the
full propose -> approve -> apply -> parity cycle without touching the shipped
files.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config

logger = logging.getLogger(__name__)

# ── Paths / seams ─────────────────────────────────────────────────────────

WEIGHT_SYNONYM = 2          # per design doc §4.4 — below every numeric gate
MAX_CANDIDATES = 600        # global cap on a single propose run (reviewable)
MAX_PER_SOURCE = 6          # cap per source phrase (keeps proposals focused)


def _candidates_path() -> Path:
    return config.DATA_DIR / "exemplar_candidates.json"


def _manifest_path() -> Path:
    return config.DATA_DIR / "auto_pattern_manifest.json"


def _exemplars_path() -> Path:
    return config.DATA_DIR / "exemplars.json"


def _vocab_path() -> Path:
    """Path of vocab.py to regenerate. MERCHANT_INTENTS_VOCAB is the test
    seam — the suite points it at a temp copy so the real file never moves."""
    env = os.environ.get("MERCHANT_INTENTS_VOCAB")
    if env:
        return Path(env)
    from . import vocab as _vocab
    return Path(_vocab.__file__).resolve()


# ── WordNet availability (optional dependency) ───────────────────────────

def wordnet_available() -> bool:
    """True when nltk + the wordnet corpus are importable. The proposal stage
    degrades gracefully when False; apply/status never need it."""
    try:
        import nltk  # noqa: F401
        nltk.data.find("corpora/wordnet")
        return True
    except Exception:
        return False


def wordnet_status() -> Dict[str, Any]:
    """Human-readable availability for the UI (install hint included)."""
    try:
        import nltk  # noqa: F401
        have_nltk = True
        try:
            nltk.data.find("corpora/wordnet")
            have_corpus = True
        except LookupError:
            have_corpus = False
    except Exception:
        have_nltk = False
        have_corpus = False
    return {
        "nltk": have_nltk,
        "wordnet": have_corpus,
        "hint": None if (have_nltk and have_corpus) else (
            "python -m pip install nltk && python -c \"import nltk; "
            "nltk.download('wordnet')\""),
    }


# ── Source phrase extraction (cold start, §5 Phase A) ─────────────────────

_SOURCE_STOP = frozenset({
    "the", "a", "an", "of", "for", "in", "on", "to", "and", "with", "by",
    "from", "at", "all", "any", "is", "are", "do", "does", "has", "have",
})

# Words whose WordNet synsets are pure noise for a merchant registry
# (numbers, particles, filler) — everything else is left for the human gate.
_SOURCE_JUNK = frozenset({
    "e", "vs", "v", "ss", "st", "rs",
})


def _literal_runs(pattern: str) -> Optional[str]:
    """Longest literal word run inside a regex pattern.

    r"\\bstatic account\\b"   -> "static account"
    r"\\be[- ]?mails?\\b"     -> "mails"          (the only literal run)
    r"\\btop \\d+\\b"         -> "top"            (\\d+ is not literal)
    Returns None when the pattern carries no usable literal phrase.

    The regex escapes (\\b, \\s, \\d ...) are dropped BEFORE run extraction
    — otherwise the 'b' of \\b glues itself onto the next word
    (\\baccount manager\\b would yield "baccount manager").
    """
    cleaned = re.sub(r"\\.", " ", (pattern or "").lower())
    cleaned = re.sub(r"[^a-z ]+", " ", cleaned)
    runs = re.findall(r"[a-z]+(?: [a-z]+)*", cleaned)
    runs = [r for r in runs if len(r) >= 3 and r not in _SOURCE_JUNK]
    if not runs:
        return None
    return max(runs, key=lambda r: (len(r.split()), len(r)))


def extract_source_phrases() -> List[Tuple[str, str]]:
    """(intent, literal phrase) pairs from live patterns + curated exemplars.

    Patterns are the primary source (they are the shipped intent language);
    the curated exemplars (data/exemplars.json, when present) add the
    hand-picked paraphrase anchors — both feed WordNet the same way.
    """
    from . import vocab
    out: List[Tuple[str, str]] = []
    seen = set()
    for intent, pats in vocab.INTENT_PATTERNS.items():
        for pattern, _w in pats:
            phrase = _literal_runs(pattern)
            if phrase:
                key = (intent, phrase)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    try:
        data = json.loads(_exemplars_path().read_text(encoding="utf-8"))
        for intent, phrases in (data.get("intents") or {}).items():
            for phrase in phrases:
                p = str(phrase).strip().lower()
                if len(p.split()) >= 2:
                    key = (intent, p)
                    if key not in seen:
                        seen.add(key)
                        out.append(key)
    except (OSError, json.JSONDecodeError, ValueError):
        pass  # exemplars missing/corrupt — patterns alone are enough
    return out


# ── WordNet expansion ────────────────────────────────────────────────────

def _wordnet_synonyms(word: str) -> List[str]:
    """Lemma names for `word` across noun/verb/adjective synsets.

    Single-word lemmas first (precise, low-noise swaps), then two-word
    lemmas (account_holder -> "account holder" — excellent precise phrases).
    Longer lemma chains are dropped: they are where WordNet's cross-domain
    noise lives ("account" -> "business relationship"), and the human
    curation gate is for filtering judgement calls, not wholesale garbage.
    Excludes the word itself.
    """
    from nltk.corpus import wordnet as wn
    singles: List[str] = []
    doubles: List[str] = []
    seen = set()
    for pos in (wn.NOUN, wn.VERB, wn.ADJ):
        for ss in wn.synsets(word, pos=pos):
            for lemma in ss.lemmas():
                name = lemma.name().lower().replace("_", " ").strip()
                if name == word or name in seen:
                    continue
                n_words = len(name.split())
                if n_words > 2:
                    continue
                if not re.fullmatch(r"[a-z]+(?: [a-z]+){0,1}", name):
                    continue
                if any(len(t) < 2 for t in name.split()):
                    continue
                seen.add(name)
                (singles if n_words == 1 else doubles).append(name)
    return sorted(singles, key=len) + sorted(doubles, key=len)


def _expand_phrase(intent: str, phrase: str,
                   live_patterns: Dict[str, List[str]],
                   conflict_index: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """One source phrase -> candidate records.

    Every content word is swapped for its WordNet synonyms in place
    ("static account" + account -> "static business relationship"), so the
    candidate stays in the intent's register. Candidates already covered by a
    live pattern of the SAME intent are dropped (novelty); candidates that
    exactly match a live pattern of ANOTHER intent are kept but flagged
    `conflict` so apply_approved() refuses them.
    """
    words = [w for w in phrase.split() if w not in _SOURCE_STOP]
    if not words:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for idx, w in enumerate(words):
        for syn in _wordnet_synonyms(w):
            cand_words = [syn if i == idx else wi for i, wi in enumerate(words)]
            cand = " ".join(cand_words).strip()
            if cand == phrase or cand in seen:
                continue
            if len(cand) < 4 or len(cand.split()) > 4:
                continue
            seen.add(cand)
            pat = r"\b" + re.escape(cand) + r"\b"
            if any(re.search(p, cand) for p in live_patterns.get(intent, [])):
                continue  # already covered by this intent — not novel
            # conflict_index maps pattern -> [intents carrying it]; a candidate
            # conflicts when its phrase matches a pattern owned by another
            # intent.
            conflicts: List[str] = []
            for cpat, cintents in conflict_index.items():
                if _matches_phrase(cpat, pat):
                    conflicts.extend(i for i in cintents if i != intent)
            out.append({
                "id": _candidate_id(intent, cand),
                "intent": intent,
                "phrase": cand,
                "source_phrase": phrase,
                "source_word": w,
                "synonym": syn,
                "weight_suggestion": WEIGHT_SYNONYM,
                "status": "pending",
                "conflict": bool(conflicts),
                "conflict_with": conflicts,
            })
            if len(out) >= MAX_PER_SOURCE:
                break
    return out


def _candidate_id(intent: str, phrase: str) -> str:
    """Stable id so re-proposing never duplicates or re-keys a candidate."""
    digest = hashlib.sha1(f"{intent}::{phrase}".encode("utf-8")).hexdigest()[:8]
    return f"c_{digest}"


def _normalize_pattern(pattern: str) -> str:
    r"""Canonical phrase form of a pattern for duplicate/conflict comparison.

    The codebase writes patterns two different ways: human-authored ones keep
    literal spaces (r"\baccount holder\b") while programmatic ones go
    through re.escape, which on Python 3.7+ escapes the space too (the space
    becomes an escaped-space token). Both match the same text — comparisons
    must normalise them or duplicates slip through and cross-intent
    conflicts go undetected."""
    p = (pattern or "").lower()
    p = re.sub(r"\\s\+", " ", p)   # \s+ -> single space
    p = re.sub(r"\\ ", " ", p)      # escaped space -> space
    p = p.replace("\\b", "").strip()
    return re.sub(r"\s+", " ", p).strip()


def _matches_phrase(pattern: str, phrase_pattern: str) -> bool:
    """True when `pattern` is a word-boundary phrase over the same words as
    `phrase_pattern` (handles escaped-space vs literal-space variants)."""
    return _normalize_pattern(pattern) == _normalize_pattern(phrase_pattern)


def _live_pattern_index() -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """(same-intent pattern lists, all-pattern index by exact string)."""
    from . import vocab
    same: Dict[str, List[str]] = {}
    exact: Dict[str, List[str]] = {}
    for intent, pats in vocab.INTENT_PATTERNS.items():
        same[intent] = [p for p, _w in pats]
        for p, _w in pats:
            exact.setdefault(p, []).append(intent)
    return same, exact


# ── Candidate store ───────────────────────────────────────────────────────

def _read_candidates() -> List[Dict[str, Any]]:
    path = _candidates_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [c for c in (data.get("candidates") or [])
                if isinstance(c, dict)]
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("exemplar_candidates.json unreadable — starting fresh")
        return []


def _write_candidates(cands: List[Dict[str, Any]], generated_at: str) -> None:
    path = _candidates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({
        "generated_at": generated_at,
        "generator": "merchant_intelligence.tasks.enrichment",
        "candidates": cands,
    }, indent=2, ensure_ascii=False)
    path.write_text(blob + "\n", encoding="utf-8")


def propose_candidates() -> Dict[str, Any]:
    """Stage 1 — expand patterns/exemplars via WordNet into pending proposals.

    Idempotent: existing candidates keep their status (approved/rejected/
    applied survive re-proposing); only brand-new phrases are added.
    """
    status = wordnet_status()
    if not (status["nltk"] and status["wordnet"]):
        return {"ok": False, "reason": "wordnet unavailable",
                "wordnet": status}
    same, exact = _live_pattern_index()
    existing = {c["id"]: c for c in _read_candidates()}
    added: List[Dict[str, Any]] = []
    total = len(existing)
    for intent, phrase in extract_source_phrases():
        if total >= MAX_CANDIDATES:
            break
        for cand in _expand_phrase(intent, phrase, same, exact):
            cid = cand["id"]
            if cid in existing:
                continue  # known — status preserved
            existing[cid] = cand
            added.append(cand)
            total += 1
            if total >= MAX_CANDIDATES:
                break
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_candidates(sorted(existing.values(),
                             key=lambda c: (c["intent"], c["phrase"])), now)
    return {
        "ok": True,
        "added": len(added),
        "total": len(existing),
        "wordnet": status,
        "generated_at": now,
    }


def candidates() -> Dict[str, Any]:
    """Read the proposal store + summary stats for the UI."""
    cands = _read_candidates()
    by_status: Dict[str, int] = {}
    for c in cands:
        st = c.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "ok": True,
        "wordnet": wordnet_status(),
        "count": len(cands),
        "by_status": by_status,
        "candidates": cands,
        "file": str(_candidates_path()),
    }


def set_status(ids: List[str], status: str) -> Dict[str, Any]:
    """Stage 2 — the curation gate: mark candidates approved or rejected.

    'applied' records are immutable (never re-approved once merged).
    """
    if status not in ("approved", "rejected"):
        return {"ok": False, "reason": f"bad status {status!r}"}
    want = set(ids or [])
    cands = _read_candidates()
    changed = 0
    for c in cands:
        if c["id"] in want and c.get("status") != "applied":
            if c.get("status") != status:
                c["status"] = status
                c["status_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                changed += 1
    if changed:
        _write_candidates(cands, time.strftime("%Y-%m-%d %H:%M:%S"))
    return {"ok": True, "changed": changed,
            "by_status": candidates()["by_status"]}


# ── Apply (stage 3) — merge + lockstep + exemplars + manifest ─────────────

def apply_approved(ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Stage 3 — merge approved candidates into the live intent config.

    For every candidate (approved by default, or the given ids):
      1. intents.json gets {"pattern": <\\bphrase\\b>, "weight": 2}
         (skipped when already present, unknown intent, or a live conflict
         with another intent).
      2. vocab.py's _DEFAULT_INTENT_PATTERNS is REGENERATED in lockstep from
         the live merged state (the [4h] parity test's guarantee).
      3. the phrase is appended to data/exemplars.json (Tier 2 exemplars).
      4. provenance is recorded in data/auto_pattern_manifest.json.

    Idempotent — applied candidates are marked and never re-applied.
    """
    from . import vocab
    cands = _read_candidates()
    if ids:
        selected = [c for c in cands if c["id"] in set(ids)
                    and c.get("status") != "applied"]
    else:
        selected = [c for c in cands
                    if c.get("status") == "approved"
                    and c.get("status") != "applied"]
    if not selected:
        return {"ok": True, "applied": [], "skipped": [],
                "note": "nothing to apply"}

    intents_cfg = vocab.get_intent_config().get("intents") or {}
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for c in selected:
        intent, phrase = c["intent"], c["phrase"]
        pattern = r"\b" + re.escape(phrase) + r"\b"
        spec = intents_cfg.get(intent)
        if spec is None:
            skipped.append({**c, "reason": "unknown intent"})
            continue
        pats = [dict(p) for p in (spec.get("patterns") or [])]
        if any(_matches_phrase(p.get("pattern"), pattern) for p in pats):
            skipped.append({**c, "reason": "already present"})
            continue
        conflicts = [i for i, s in intents_cfg.items()
                     if i != intent and any(
                         _matches_phrase(p.get("pattern"), pattern)
                         for p in (s.get("patterns") or []))]
        if conflicts:
            skipped.append({**c, "reason": f"conflicts with {conflicts}"})
            continue
        if c.get("conflict"):
            skipped.append({**c, "reason": "flagged conflicting"})
            continue
        new_spec = dict(spec)
        new_spec["patterns"] = pats + [{"pattern": pattern,
                                        "weight": WEIGHT_SYNONYM}]
        try:
            vocab.save_intent_config(intent, new_spec)
        except OSError as exc:
            skipped.append({**c, "reason": f"write failed: {exc}"})
            continue
        applied.append(c)
        intents_cfg = vocab.get_intent_config().get("intents") or {}

    if applied:
        regenerate_vocab_defaults()
        _append_exemplars([c["phrase"] for c in applied])
        _append_manifest(applied)
        applied_ids = {c["id"] for c in applied}
        store = _read_candidates()
        for c in store:
            if c["id"] in applied_ids:
                c["status"] = "applied"
                c["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_candidates(store, time.strftime("%Y-%m-%d %H:%M:%S"))
        vocab.reload_intents()

    return {
        "ok": True,
        "applied": [{k: c.get(k) for k in ("id", "intent", "phrase",
                                            "source_phrase", "synonym")}
                    for c in applied],
        "skipped": [{**{k: c.get(k) for k in ("id", "intent", "phrase")},
                     "reason": c.get("reason")} for c in skipped],
        "hot_reloaded": bool(applied),
        "parity_ok": parity_ok(),
    }


def _append_exemplars(phrases: List[str]) -> None:
    """Append the approved phrases to data/exemplars.json for Tier 2 (the
    runtime encoder picks them up on next process start). Idempotent."""
    path = _exemplars_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        intents = data.get("intents") or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return  # no exemplar file — the keyword cold start still covers us
    changed = False
    for c in _read_candidates():
        if c["phrase"] in phrases:  # `phrases` IS the applied set
            bucket = intents.setdefault(c["intent"], [])
            if c["phrase"] not in bucket:
                bucket.append(c["phrase"])
                changed = True
    if not changed:
        return
    blob = json.dumps({"intents": intents}, indent=2, ensure_ascii=False)
    path.write_text(blob + "\n", encoding="utf-8")
    # Keep the exemplar manifest's md5 honest (same shape as build_exemplars).
    manifest_path = config.DATA_DIR / "exemplar_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        manifest = {}
    manifest["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["exemplars_md5"] = hashlib.md5(blob.encode("utf-8")).hexdigest()
    manifest["phrase_count"] = sum(len(v) for v in intents.values())
    manifest["per_intent"] = {i: len(v) for i, v in sorted(intents.items())}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False)
                             + "\n", encoding="utf-8")


def _append_manifest(applied: List[Dict[str, Any]]) -> None:
    """Provenance: every applied auto-pattern is auditable on disk even
    though data/ is gitignored (design doc §9)."""
    path = _manifest_path()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError, ValueError):
        entries = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for c in applied:
        entries.append({
            "pattern": r"\b" + re.escape(c["phrase"]) + r"\b",
            "intent": c["intent"],
            "weight": WEIGHT_SYNONYM,
            "phrase": c["phrase"],
            "source_phrase": c.get("source_phrase"),
            "source_word": c.get("source_word"),
            "synonym": c.get("synonym"),
            "source": "wordnet",
            "approved_at": now,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


# ── Lockstep vocab.py regeneration ────────────────────────────────────────

def _repr_pattern(pattern: str) -> str:
    """Render a pattern the way vocab.py writes it (raw string when it has
    backslashes, plain quoted string otherwise)."""
    if '"' in pattern:
        return repr(pattern)
    if "\\" in pattern:
        return 'r"' + pattern + '"'
    return '"' + pattern + '"'


def _serialize_patterns(patterns: Dict[str, List[Tuple[str, int]]]) -> str:
    """vocab.py-style block for _DEFAULT_INTENT_PATTERNS. Deterministic
    (sorted intents, one pair per line) so regeneration is diff-stable.
    Ends with the dict's closing brace."""
    lines: List[str] = []
    for intent in sorted(patterns):
        lines.append(f"    {intent!r}: [")
        for p, w in patterns[intent]:
            lines.append(f"        ({_repr_pattern(p)}, {w}),")
        lines.append("    ],")
    lines.append("}")
    return "\n".join(lines) + "\n"


def regenerate_vocab_defaults() -> None:
    """Rewrite _DEFAULT_INTENT_PATTERNS in vocab.py from the live merged
    state (config file wins; defaults fill gaps). This is what keeps the
    [4h] parity check green after any config-side merge — feedback's
    apply_pattern() calls this too.

    Raises ValueError when the expected block structure is not found (the
    file layout changed) so a silent no-op can never fake a sync.
    """
    from . import vocab
    path = _vocab_path()
    text = path.read_text(encoding="utf-8")
    marker = "_DEFAULT_INTENT_PATTERNS"
    start = text.index(marker)
    brace = text.index("{", start)
    close = re.search(r"^}", text[brace:], re.M)
    if close is None:
        raise ValueError(f"could not locate _DEFAULT_INTENT_PATTERNS block "
                         f"in {path}")
    end = brace + close.end()
    block = _serialize_patterns(vocab.INTENT_PATTERNS)
    new_text = text[:brace + 1] + "\n" + block + text[end:]
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    # Keep the IN-MEMORY defaults in sync too — the [4h] parity check reads
    # the module attribute, not the file, so a fresh process would see the
    # new file but the current one would still hold the old defaults.
    vocab._DEFAULT_INTENT_PATTERNS = {
        k: [(p, w) for p, w in v] for k, v in vocab.INTENT_PATTERNS.items()
    }


def parity_ok() -> bool:
    """The [4h] gate itself: shipped config == code defaults."""
    from . import vocab
    return vocab.INTENT_PATTERNS == vocab._DEFAULT_INTENT_PATTERNS


# ── Manifest read (for the UI) ────────────────────────────────────────────

def manifest() -> Dict[str, Any]:
    path = _manifest_path()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError, ValueError):
        entries = []
    by_intent: Dict[str, int] = {}
    for e in entries:
        by_intent[e.get("intent", "?")] = by_intent.get(e.get("intent"), 0) + 1
    return {"ok": True, "count": len(entries), "by_intent": by_intent,
            "entries": entries[-200:], "file": str(_manifest_path())}


if __name__ == "__main__":
    # Diagnostic entry point — the CLI wrapper is scripts/enrich_intents.py.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(candidates(), indent=2, ensure_ascii=False))
