"""
calibration.py — Confidence calibration for the task engine.

Logs every REAL request the engine executed: the predicted intent and its
confidence, plus the intent actually chosen — either the auto-routed one
(source="auto", always accepted) or the option the user picked after a
clarification prompt, tagged by what the user did:

  source="accept"    the user confirmed the engine's predicted intent
  source="override"  the user corrected it to a different intent

From that history it fits the ask-for-confirmation thresholds used by
suggest_clarification:

  ask_threshold   confidence below which a request should be flagged for
                  confirmation (low-confidence requests get asked first)
  gap_threshold   score gap under which the top two intents "race"

The ask_threshold fitter is a banded acceptance scan: it buckets decisions
by confidence (0-19, 20-39, ... 80-100), finds the highest band whose
acceptance is below the target, and sets the ask threshold just above it.

The gap_threshold fitter does the same over the logged RACE OUTCOMES —
clarification picks that were asked because two intents raced, each carrying
the top-2 score gap it was asked at. A band where users kept correcting the
prediction means races at that gap are genuinely ambiguous (keep the window
that wide); a solid band means the ask was unnecessary there (tighten).

No machine-learning dependency — pure statistics on the collected log,
explainable in the UI.

The log lives in data/request_log.jsonl (survives DB rebuilds) — override
with the MERCHANT_CALIBRATION_FILE env var (tests use a temp file).

Phase 3 (design doc §7) adds a SECOND evidence source: the Tier-2
shadow-review labels (semantic.py's label_entry). Those labels are
accept/override evidence for the auto-run band — the band clarification
outcomes never cover. fit_tier2() folds them into per-intent Tier-2 gates:

  - per-intent THRESHOLD once an intent has >= TIER2_PER_INTENT_MIN labeled
    would-act decisions (same banded acceptance scan as the ask fit),
  - a GLOBAL margin fit from all labeled would-act decisions (margin is
    intent competition, not identity),
  - a conservative LOWERING signal from correct would-not decisions: an
    intent whose picks are right but sit under the gate (the ONNX
    under-mapped cosine band) gets its threshold dropped to the floor of
    the highest band with >= 3 such samples.

semantic.resolve() consults fit_tier2() per request; the review labels live
in data/tier2_shadow_review.jsonl (MERCHANT_TIER2_REVIEW_FILE override).
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

# Fit tuning.
MIN_SAMPLES = 20          # decisions required before the fit is trusted
TARGET_ACCEPTANCE = 0.80  # acceptance below this in a band is "ask here"
DEFAULT_ASK_THRESHOLD = 60  # matches engine.CLARIFY_TOP_MAX
DEFAULT_GAP_THRESHOLD = 4.0  # matches engine.CLARIFY_GAP
ASK_FLOOR = 20            # never ask below this confidence (noise floor)
ASK_CEILING = 90          # never auto-run above this confidence

# Log hygiene: prune the log once it grows past ~4000 entries (~1MB), keeping
# the most recent MAX_LOG_ENTRIES — the fit only needs recent evidence and a
# bounded file keeps params()/fit() reads cheap on the per-request hot path.
MAX_LOG_ENTRIES = 2000
_MAX_LOG_BYTES = 1_000_000

# Confidence bands for the acceptance scan (low, high, label).
_BANDS = [(0, 20, "0-19"), (20, 40, "20-39"), (40, 60, "40-59"),
          (60, 80, "60-79"), (80, 101, "80-100")]

# Score-gap bands for the race-window fit (the top-2 score gap the engine
# uses to decide when two intents "race"). Same banded scan as the
# confidence fit, but over the logged race outcomes (see _race_entries).
# The widest band (4-6) is only reachable after the window is widened — the
# default 4.0 cap means no outcomes are ever observed above it, and an
# empty band is skipped (no evidence = don't move the threshold).
_GAP_BANDS = [(0.0, 1.0, "0-1"), (1.0, 2.0, "1-2"), (2.0, 3.0, "2-3"),
              (3.0, 4.0, "3-4"), (4.0, 6.0, "4-6")]
GAP_FLOOR = 0.5      # never race-check below this gap (noise floor)
GAP_CEILING = 6.0    # never widen the race window beyond this gap

# ── Tier-2 gates (Phase 3) ────────────────────────────────────────────────
# Fitted from the §7 shadow-review labels (accept/override evidence for the
# auto-run band). Threshold is per intent (an intent's reliability is its
# own); margin is global (intent competition, not identity).
TIER2_PER_INTENT_MIN = 20   # labeled would-act decisions per intent before fit
TIER2_LOWER_FLOOR = 40      # never fit a Tier-2 threshold below this (noise)
TIER2_MARGIN_FLOOR = 0
TIER2_MARGIN_CEILING = 40
_T2_BANDS = [(0, 20, "0-19"), (20, 40, "20-39"), (40, 60, "40-59"),
             (60, 80, "60-79"), (80, 101, "80-100")]
_T2_MARGIN_BANDS = [(0, 10, "0-9"), (10, 20, "10-19"), (20, 30, "20-29"),
                    (30, 40, "30-39"), (40, 101, "40+")]

_lock = threading.Lock()

# mtime/size-keyed cache: params()/fit()/stats() run on every /api/task call
# (suggest_clarification reads the threshold), so an unchanged log must not
# be re-parsed. Invalidated by record()/reset().
_cache: Dict[str, Any] = {"mtime_ns": None, "size": None, "entries": None}


def _log_path() -> Path:
    """The decision log file (env override wins — tests use a temp file)."""
    override = os.environ.get("MERCHANT_CALIBRATION_FILE")
    if override:
        return Path(override)
    return config.DATA_DIR / "request_log.jsonl"


def record(text: str, predicted: str, confidence: int, chosen: str,
           source: str, gap: Optional[float] = None) -> None:
    """Append one real request decision to the calibration log.

    predicted  the intent the engine would have routed
    confidence 0-100 confidence of that prediction
    chosen     the intent that actually ran (== predicted for auto-runs;
               the user's pick for clarification/override)
    source     "auto" (engine routed, unchallenged) | "accept" (user
               confirmed the prediction) | "override" (user corrected it)
               — how chosen was decided
    gap        optional top-2 score gap at decision time, present only when
               the request had two+ scored intents (a RACE context). The
               race-outcome fitter uses clarification picks' gaps to learn
               the race window (gap_threshold); auto-runs log it too but
               never count toward the fit.
    """
    entry = {
        "ts": time.time(),
        "text": (text or "")[:300],
        "predicted": predicted,
        "confidence": int(confidence),
        "chosen": chosen,
        "source": source,
        "accepted": chosen == predicted,
    }
    if gap is not None:
        entry["gap"] = round(float(gap), 3)
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            _maybe_prune(path)
        _invalidate_cache()
    except OSError as exc:
        logger.warning("calibration log write failed: %s", exc)


def _maybe_prune(path: Path) -> None:
    """Trim the log to the most recent MAX_LOG_ENTRIES once it passes the
    size cap — keeps the file bounded (the fit only needs recent evidence).
    Runs rarely (only after ~4000 appends), so the read cost is amortised."""
    try:
        if path.stat().st_size < _MAX_LOG_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) > MAX_LOG_ENTRIES:
            path.write_text("\n".join(lines[-MAX_LOG_ENTRIES:]) + "\n",
                            encoding="utf-8")
    except OSError:
        pass


def _invalidate_cache() -> None:
    with _lock:
        _cache["mtime_ns"] = None
        _cache["size"] = None
        _cache["entries"] = None


def load() -> List[Dict[str, Any]]:
    """All logged decisions, oldest first. Corrupt lines are skipped.

    Cached by file mtime+size so the per-request threshold read in
    suggest_clarification doesn't re-parse an unchanged log."""
    path = _log_path()
    try:
        st = path.stat()
    except OSError:
        return []
    with _lock:
        if (_cache["entries"] is not None
                and _cache["mtime_ns"] == st.st_mtime_ns
                and _cache["size"] == st.st_size):
            return _cache["entries"]
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
    with _lock:
        _cache["mtime_ns"] = st.st_mtime_ns
        _cache["size"] = st.st_size
        _cache["entries"] = out
    return out


def reset() -> int:
    """Delete the decision log. Returns the number of entries removed."""
    path = _log_path()
    if not path.exists():
        return 0
    with _lock:
        try:
            n = sum(1 for _ in path.open("r", encoding="utf-8",
                                         errors="ignore"))
        except OSError:
            n = 0
        try:
            path.unlink()
        except OSError:
            pass
        _cache["mtime_ns"] = None
        _cache["size"] = None
        _cache["entries"] = None
    return n


def _acceptance(entries: List[Dict[str, Any]]) -> Optional[float]:
    if not entries:
        return None
    return sum(1 for e in entries if e.get("accepted")) / len(entries)


def _band_of(conf: int) -> Optional[str]:
    for lo, hi, label in _BANDS:
        if lo <= conf < hi:
            return label
    return None


def _race_entries(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clarification decisions that were RACES — they carry the top-2 score
    gap they were asked at. Auto-runs never count: a race that auto-ran was
    confident (top confidence above the ask bound), so its outcome says
    nothing about the race window."""
    return [e for e in decisions
            if e.get("gap") is not None
            and e.get("source") in ("accept", "override", "clarify")]


def _gap_of(entry: Dict[str, Any]) -> Optional[float]:
    """Numeric gap of a logged entry, or None when missing/corrupt — a
    hand-edited log line must never crash the per-request threshold read."""
    try:
        return float(entry["gap"])
    except (KeyError, TypeError, ValueError):
        return None


def _band_of_gaps(races: List[Dict[str, Any]], lo: float, hi: float
                  ) -> List[Dict[str, Any]]:
    return [e for e in races
            if (g := _gap_of(e)) is not None and lo <= g < hi]


def stats() -> Dict[str, Any]:
    """Summary of the decision history (no fitting)."""
    entries = load()
    decisions = [e for e in entries if e.get("chosen")]
    bands = []
    for lo, hi, label in _BANDS:
        band = [e for e in decisions if lo <= int(e.get("confidence", 0)) < hi]
        if band:
            bands.append({
                "band": label,
                "samples": len(band),
                "accepted": sum(1 for e in band if e.get("accepted")),
                "acceptance": round(_acceptance(band) or 0, 3),
            })
    per_intent: Dict[str, Dict[str, int]] = {}
    for e in decisions:
        bucket = per_intent.setdefault(e.get("predicted", "?"),
                                       {"samples": 0, "accepted": 0})
        bucket["samples"] += 1
        if e.get("accepted"):
            bucket["accepted"] += 1
    # Race-window acceptance: the clarification picks that were asked
    # because two intents raced, bucketed by the top-2 gap at ask time.
    races = _race_entries(decisions)
    gap_bands = []
    for lo, hi, label in _GAP_BANDS:
        band = _band_of_gaps(races, lo, hi)
        if band:
            gap_bands.append({
                "band": label,
                "samples": len(band),
                "accepted": sum(1 for e in band if e.get("accepted")),
                "acceptance": round(_acceptance(band) or 0, 3),
            })
    return {
        "total_entries": len(entries),
        "decisions": len(decisions),
        "accepted": sum(1 for e in decisions if e.get("accepted")),
        "acceptance": round(_acceptance(decisions) or 0, 3),
        "bands": bands,
        "gap_bands": gap_bands,
        "race_decisions": len(races),
        "per_intent": [
            {"intent": k, "samples": v["samples"],
             "accepted": v["accepted"],
             "acceptance": round(v["accepted"] / v["samples"], 3)}
            for k, v in sorted(per_intent.items())
        ],
        "sources": {
            "auto": sum(1 for e in decisions if e.get("source") == "auto"),
            # accept/override come from the API tagging picks; legacy "clarify"
            # entries (recorded before the split) are folded by their accepted
            # flag so the counts stay truthful across upgrades.
            "accept": sum(1 for e in decisions
                          if e.get("source") == "accept"
                          or (e.get("source") == "clarify" and e.get("accepted"))),
            "override": sum(1 for e in decisions
                            if e.get("source") == "override"
                            or (e.get("source") == "clarify" and not e.get("accepted"))),
            # Any other/unknown source value — the buckets above always sum
            # to decisions, so the UI totals can never silently disagree.
            "other": sum(1 for e in decisions
                         if e.get("source") not in ("auto", "accept",
                                                    "override", "clarify")),
        },
    }


def fit() -> Dict[str, Any]:
    """Fit the ask + race-window thresholds from the logged decisions.

    ask_threshold (confidence): bands are scanned high -> low. The threshold
    is the confidence below which the engine should ask before running; it
    sits just above the highest band whose acceptance is below the target.
    Bands with too few samples are skipped (no evidence = don't move it).

    gap_threshold (race window): the same scan over the logged RACE
    outcomes, bucketed by the top-2 score gap they were asked at. A band
    where users repeatedly corrected the prediction keeps the window at
    least that wide; a solid band tightens it to the band's floor. Empty
    bands (races never asked there) are skipped — no evidence means the
    window is left where it was, so it can never widen from thin air.

    Known consequence of that honesty: once the window tightens, races
    beyond it stop being asked, so no new evidence accrues in the wider
    bands and the fit never re-widens from race data alone — a too-tight
    window is recovered via the saved-interpretation preferences or the
    decision-log reset, never silently.
    """
    entries = load()
    decisions = [e for e in entries if e.get("chosen")]
    ask = DEFAULT_ASK_THRESHOLD
    if len(decisions) >= MIN_SAMPLES:
        # Scan from the top band down. Evidence that a band is SOLID (high
        # acceptance) lets the threshold drop to that band's floor; the first
        # band with poor acceptance raises it to the band's top edge (ask
        # everything below it). Bands with too few samples are skipped — no
        # evidence means the threshold is left where it was.
        for lo, hi, label in reversed(_BANDS):
            band = [e for e in decisions
                    if lo <= int(e.get("confidence", 0)) < hi]
            if len(band) < 3:
                continue  # not enough evidence in this band
            acc = _acceptance(band) or 0
            if acc < TARGET_ACCEPTANCE:
                # Poor acceptance here — everything below the band's top
                # edge should be confirmed before running.
                ask = min(hi, ASK_CEILING)
                break
            # This band is solid — requests at this confidence are safe to
            # auto-run, so the threshold can sit at the band's floor.
            ask = min(ask, lo)
    ask = max(ASK_FLOOR, min(ask, ASK_CEILING))
    # ── Race-window fit ──
    races = _race_entries(decisions)
    gap_active = len(races) >= MIN_SAMPLES
    gap = DEFAULT_GAP_THRESHOLD
    if gap_active:
        # Scan from the widest band down (same solid/poor logic as above,
        # over top-2 score gaps): poor acceptance at a gap band keeps the
        # window at least that wide; a solid band tightens it to the floor.
        for lo, hi, label in reversed(_GAP_BANDS):
            band = _band_of_gaps(races, lo, hi)
            if len(band) < 3:
                continue
            acc = _acceptance(band) or 0
            if acc < TARGET_ACCEPTANCE:
                gap = min(hi, GAP_CEILING)
                break
            gap = min(gap, lo)
    gap = round(max(GAP_FLOOR, min(gap, GAP_CEILING)), 1)
    return {
        "active": len(decisions) >= MIN_SAMPLES,
        "samples": len(decisions),
        "ask_threshold": ask,
        "gap_threshold": gap,
        "gap_active": gap_active,
        "race_samples": len(races),
        "default_ask": DEFAULT_ASK_THRESHOLD,
        "default_gap": DEFAULT_GAP_THRESHOLD,
        "target_acceptance": TARGET_ACCEPTANCE,
        "min_samples": MIN_SAMPLES,
        "gap_min_samples": MIN_SAMPLES,
        "gap_floor": GAP_FLOOR,
        "gap_ceiling": GAP_CEILING,
        "fitted_at": time.time(),
    }


def params() -> Dict[str, Any]:
    """Live thresholds for suggest_clarification — fitted when the log has
    enough evidence, otherwise the built-in defaults.

    ask_threshold and gap_threshold are fitted INDEPENDENTLY: the gap fit
    activates once enough race outcomes (clarification picks with a top-2
    gap) are logged, so a fitted ask bound never waits on race data (and
    vice versa)."""
    f = fit()
    return {
        "active": f["active"],
        "ask_threshold": f["ask_threshold"],
        "gap_threshold": f["gap_threshold"],
        "gap_active": f["gap_active"],
    }


# ── Tier 2: per-intent gates from the §7 shadow-review labels ─────────────
# The spot-check labels (semantic.label_entry) are accept/override evidence
# for the auto-run band — the band clarification outcomes never cover.
# fit_tier2() reads the labels + shadow entries (both file seams, so tests
# stay hermetic) and folds them into per-intent gates that
# semantic.resolve() consults per request (Phase 3 of design doc §7).
_t2_cache: Dict[str, Any] = {"key": None, "fit": None}


def _t2_cache_key() -> str:
    """State of the Tier-2 fit inputs (review + shadow files)."""
    from .tasks import semantic
    parts = []
    for p in (semantic._review_path(), semantic._shadow_path()):
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("missing")
    return "|".join(parts)


def _scan_t2_confidence(rows: List[Dict[str, Any]], start: int) -> int:
    """Banded acceptance scan over the confidence axis (same solid/poor
    logic as fit()): poor acceptance in a band raises the gate to the band's
    top edge; a solid band drops it to the floor. Bands with <3 samples are
    skipped — no evidence means the gate is left where it was."""
    threshold = start
    for lo, hi, _label in reversed(_T2_BANDS):
        band = [r for r in rows
                if lo <= int(r.get("tier2_confidence", 0)) < hi]
        if len(band) < 3:
            continue
        acc = sum(1 for r in band if r.get("label", {}).get("correct"))
        acc = acc / len(band)
        if acc < TARGET_ACCEPTANCE:
            threshold = min(hi, ASK_CEILING)
            break
        threshold = min(threshold, lo)
    return threshold


def _scan_t2_margin(rows: List[Dict[str, Any]], start: int) -> int:
    """Banded acceptance scan over the margin axis (calibrated-point gap
    over the runner-up intent)."""
    margin = start
    for lo, hi, _label in reversed(_T2_MARGIN_BANDS):
        band = [r for r in rows
                if lo <= float(r.get("tier2_margin", 0) or 0) < hi]
        if len(band) < 3:
            continue
        acc = sum(1 for r in band if r.get("label", {}).get("correct"))
        acc = acc / len(band)
        if acc < TARGET_ACCEPTANCE:
            margin = min(hi, TIER2_MARGIN_CEILING)
            break
        margin = min(margin, lo)
    return margin


def _t2_lower_floor(correct_wn: List[Dict[str, Any]],
                    threshold: int) -> Optional[int]:
    """Highest confidence band (below the current threshold) with >=3
    correct would-not picks -> its floor. Those picks were RIGHT but never
    acted — evidence the gate sits too high for this intent (the ONNX
    under-mapped cosine band the doc flags). None when nothing qualifies."""
    best: Optional[int] = None
    for lo, hi, _label in _T2_BANDS:
        if lo >= threshold:
            continue
        band = [r for r in correct_wn
                if lo <= int(r.get("tier2_confidence", 0)) < hi]
        if len(band) >= 3:
            best = lo
    return best


def fit_tier2() -> Dict[str, Any]:
    """Fit per-intent Tier-2 gates from the §7 shadow-review labels.

    Per intent (keyed by tier2_intent): a THRESHOLD once >=
    TIER2_PER_INTENT_MIN labeled would-act decisions exist, fitted by the
    same banded acceptance scan as the ask threshold — an intent whose
    high-confidence auto-runs keep getting marked wrong gets its gate
    raised, a solid one drops toward its floor. A correct would-not pick is
    a missed opportunity: >=3 of them in a band below the gate lower it to
    that band's floor (never below TIER2_LOWER_FLOOR).

    Margin is a SINGLE global fit over all labeled would-act decisions
    (margin is intent competition, not identity).

    Returns the fitted state plus per-intent progress (samples/needed) so
    the UI can show "7/20 labeled" before a gate exists. Cached by review +
    shadow file state; label_entry's rewrite bumps the mtime, so the next
    resolve() sees the new gates."""
    from .tasks import semantic
    key = _t2_cache_key()
    with _lock:
        if _t2_cache["key"] == key:
            return _t2_cache["fit"]
    default_threshold = semantic.SEMANTIC_THRESHOLD
    default_margin = semantic.SEMANTIC_MARGIN
    labels = semantic.read_review()
    per: Dict[str, Dict[str, Any]] = {}
    all_wa: List[Dict[str, Any]] = []
    for e in semantic.read_shadow():
        lbl = labels.get(semantic.entry_id(e))
        if not lbl:
            continue
        row = dict(e)
        row["label"] = lbl
        intent = e.get("tier2_intent") or "?"
        b = per.setdefault(intent, {"wa": [], "wn_correct": []})
        if e.get("tier2_would_act"):
            b["wa"].append(row)
            all_wa.append(row)
        elif lbl.get("correct"):
            b["wn_correct"].append(row)
    per_intent: Dict[str, Dict[str, Any]] = {}
    for intent, b in per.items():
        wa = b["wa"]
        correct = sum(1 for r in wa if r["label"].get("correct"))
        if len(wa) < TIER2_PER_INTENT_MIN:
            per_intent[intent] = {
                "samples": len(wa),
                "needed": TIER2_PER_INTENT_MIN,
                "threshold": None,
                "precision": (round(correct / len(wa), 3) if wa else None),
                "would_not_correct": len(b["wn_correct"]),
            }
            continue
        threshold = _scan_t2_confidence(wa, default_threshold)
        floor = _t2_lower_floor(b["wn_correct"], threshold)
        if floor is not None:
            threshold = min(threshold, floor)
        threshold = max(TIER2_LOWER_FLOOR, min(threshold, ASK_CEILING))
        per_intent[intent] = {
            "samples": len(wa),
            "needed": TIER2_PER_INTENT_MIN,
            "threshold": threshold,
            "precision": round(correct / len(wa), 3),
            "would_not_correct": len(b["wn_correct"]),
        }
    margin: Optional[int] = None
    if len(all_wa) >= TIER2_PER_INTENT_MIN:
        margin = max(TIER2_MARGIN_FLOOR,
                     min(_scan_t2_margin(all_wa, default_margin),
                         TIER2_MARGIN_CEILING))
    fitted = {
        "active": any(v["threshold"] is not None
                      for v in per_intent.values()),
        "fitted_at": time.time(),
        "per_intent": per_intent,
        "margin": margin,
        "labeled": sum(len(b["wa"]) + len(b["wn_correct"])
                       for b in per.values()),
        "defaults": {"threshold": default_threshold,
                      "margin": default_margin},
        "min_samples": TIER2_PER_INTENT_MIN,
        "lower_floor": TIER2_LOWER_FLOOR,
    }
    with _lock:
        _t2_cache["key"] = key
        _t2_cache["fit"] = fitted
    return fitted
