"""Tasks / intelligence router — intent execution, self-improvement loop,
synonym curation, shadow review, audit, ingestion ledger, calibration,
preferences, intents, and engine settings.

Handlers moved verbatim from api.py during the router split; paths and
response shapes are unchanged.
"""

import json
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_shared import (
    _audit,
    _log_task_request,
    TaskRequest,
)

router = APIRouter()


class SuggestionAction(BaseModel):
    ngram: str = ""
    intent: str = ""
    weight: int = 0


class SynonymStatusRequest(BaseModel):
    ids: List[str] = []
    status: str = "approved"  # "approved" | "rejected"


class SynonymApplyRequest(BaseModel):
    ids: Optional[List[str]] = None  # None = apply all approved


class ShadowReviewLabelRequest(BaseModel):
    entry_id: str = ""
    correct: bool = True
    intent: str = ""   # optional: the intent it SHOULD have been (on a miss)
    note: str = ""


class PreferenceForgetRequest(BaseModel):
    key: str = ""


class IntentPattern(BaseModel):
    pattern: str
    weight: int = 1


class IntentUpdateRequest(BaseModel):
    intent: str
    patterns: list[IntentPattern] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    # Per-intent typo-tolerance toggle (see vocab.INTENT_FUZZY). None keeps
    # the current value; false restricts this intent to exact regex patterns.
    fuzzy: Optional[bool] = None


class SettingsUpdateRequest(BaseModel):
    decisive_match_threshold: Optional[float] = Field(
        None, ge=0.0, le=100.0)
    # Tier-2 semantic rollout state: "off" | "shadow" | "enabled"
    # (validated in the handler — same choices as settings._MODE_SPEC).
    semantic_tier_mode: Optional[str] = None


@router.post("/api/task")
def task(req: TaskRequest):
    """Natural-language task interpreter (feature: paste a request).

    Detects whether the pasted text is a task (multi-line block + identifiers
    + instruction words) vs a plain merchant search. When it IS a task, plans
    and executes a step pipeline (e.g. TIDs -> MX codes -> static accounts +
    beneficiaries) and returns a render-ready table.

    Response:
      is_task: false              -> caller should run a normal /api/search
      is_task: true               -> intent, pipeline, columns, rows, not_found, summary
      needs_clarification: true   -> the request read ambiguously ("account
        details"), so NO pipeline ran. The caller renders `question` +
        `options` and re-posts with the chosen `intent` to force that run.

    req.intent: an explicit interpretation the caller already chose (from a
    clarification prompt) — detect_task forces exactly that intent.
    """
    from merchant_intelligence import tasks
    from merchant_intelligence.calibration import record as cal_record
    text = (req.text or "").strip()
    override = (req.intent or "").strip() or None
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        detected = tasks.detect_task(text, intent_override=override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit("task", json.dumps({"text": text[:300],
                               "intent": (detected or {}).get("intent")}))
    if not detected:
        return {"is_task": False, "reason": "looks like a normal search"}
    # Referential follow-up ("get the tids for the above merchant"): the
    # merchant comes from the PREVIOUS request, not this text. When the request
    # names no entity of its own, resolve the reference against the last
    # remembered context; with no context the pipeline answers with an honest
    # "no merchant found" message.
    own_entity = ((detected.get("identifier_count") or 0) > 0
                  or bool(detected.get("names"))
                  or bool(detected.get("segment")))
    if detected.get("references_previous") and not own_entity:
        tasks.inherit_reference(detected)
    summary = {k: detected.get(k) for k in
               ("intent", "intents", "identifier_count",
                "has_instruction", "multiline", "llm_refined", "confidence",
                "references_previous", "context_inherited", "key_merchants")}
    # Remember this request's entities as the follow-up context for the next
    # "the above merchant" style request. Requests without entities (a segment
    # with no merchant) never clobber the previous merchant.
    tasks.remember_entities(detected.get("identifiers"), detected.get("names"))
    # First pass (no explicit choice): ask when the request is ambiguous.
    if not override:
        clarify = tasks.suggest_clarification(text, detected)
        if clarify:
            # Remembered choice: the user saved an interpretation for this
            # exact phrase before — auto-run it, and tell the UI which one.
            auto = clarify.get("auto_pick")
            if auto:
                try:
                    forced = tasks.detect_task(text, intent_override=auto)
                except ValueError:
                    forced = None
                if forced:
                    try:
                        base = tasks.detect_task(text)
                    except ValueError:
                        base = None
                    predicted = (base["intent"] if base else
                                 detected["intent"])
                    conf = base.get("confidence", 0) if base else 0
                    # A remembered choice that matches the prediction is an
                    # ACCEPT; one that overrides it is an OVERRIDE — the
                    # calibration fitter learns which confidence bands the
                    # user corrects vs confirms. A request that is only a
                    # task BECAUSE of the pick (unforced detect_task is
                    # None) can't be a confirmation — the engine never
                    # predicted, so tag it as an override.
                    src = "accept"
                    if base is None or auto != predicted:
                        src = "override"
                    cal_record(text, predicted, conf, auto, source=src,
                               gap=tasks.top_two_gap(base or detected))
                    result = tasks.execute_task(forced)
                    _log_task_request(forced, result)
                    result["is_task"] = True
                    # Rebuild the summary from the FORCED task so the shown
                    # intent matches what actually ran (the un-forced
                    # detection would report change_details while the saved
                    # choice ran static_account).
                    result["detected"] = {k: forced.get(k) for k in
                                           ("intent", "intents",
                                            "identifier_count",
                                            "has_instruction", "multiline",
                                            "llm_refined", "confidence")}
                    result["used_preference"] = auto
                    return result
            return {
                "is_task": True,
                "needs_clarification": True,
                "question": clarify["question"],
                "options": clarify["options"],
                "detected": summary,
            }
        # Auto-routed: the engine's pick ran without a challenge — record it
        # as an accepted decision (implicit feedback for calibration). The
        # top-2 gap rides along when two+ intents scored (a race context)
        # so the gap_threshold fitter has the data it needs.
        cal_record(text, detected["intent"], detected.get("confidence", 0),
                   detected["intent"], source="auto",
                   gap=tasks.top_two_gap(detected))
    else:
        # The user picked an interpretation (clarification card / intent
        # override). Record predicted-vs-chosen so the fitter can learn
        # which confidence bands get corrected — tagged accept when the
        # user confirmed the engine's guess, override when they corrected it.
        try:
            base = tasks.detect_task(text)
        except ValueError:
            base = None
        predicted = base["intent"] if base else detected["intent"]
        conf = base.get("confidence", 0) if base else 0
        chosen = detected["intent"]
        # A request that is only a task because of the override (unforced
        # detect_task returns None) means the engine never predicted — that
        # can't be a confirmation, so it's tagged as an override.
        src = "accept"
        if base is None or chosen != predicted:
            src = "override"
        cal_record(text, predicted, conf, chosen, source=src,
                   gap=tasks.top_two_gap(base or detected))
        # "Remember my choice": persist phrase -> chosen intent so future
        # identical requests auto-run it (see suggest_clarification).
        if req.remember and override:
            try:
                from merchant_intelligence import preferences
                preferences.learn(text, override, base)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "failed to remember choice: %s", exc)
    result = tasks.execute_task(detected)
    _log_task_request(detected, result)
    result["is_task"] = True
    result["detected"] = summary
    return result


@router.get("/api/feedback/suggestions")
def feedback_suggestions():
    """Self-improvement loop status: mined pattern suggestions + outcome stats.

    Suggestions come from real corrections — clarification overrides (user
    picked a different intent than predicted) and rephrased requests (a
    request that returned nothing was re-asked with new wording). Only
    n-grams with >= 3 corroborating samples are suggested; applying writes
    them to intents.json (hot-reloaded), rejecting records them so they
    never resurface.
    """
    from merchant_intelligence import feedback
    return feedback.report()


@router.post("/api/feedback/suggestions/apply")
def suggestion_apply(req: SuggestionAction):
    """Accept a mined suggestion: write it to intents.json + hot-reload."""
    from merchant_intelligence import feedback
    spec = feedback.apply_pattern(req.ngram.strip(),
                                  (req.intent or "").strip().lower(),
                                  req.weight or None)
    if spec is None:
        raise HTTPException(status_code=400,
                            detail="unknown intent or empty phrase")
    return {"ok": True, "ngram": req.ngram.strip(),
            "intent": (req.intent or "").strip().lower(),
            "spec": spec}


@router.post("/api/feedback/suggestions/reject")
def suggestion_reject(req: SuggestionAction):
    """Reject a mined suggestion so it never resurfaces."""
    from merchant_intelligence import feedback
    feedback.reject(req.ngram.strip(), (req.intent or "").strip().lower())
    return {"ok": True}


@router.get("/api/synonyms")
def get_synonym_candidates():
    """Tier-1 WordNet proposals (design doc §4): pending/approved/rejected
    candidate phrases grouped for curation on the Rule Engine page."""
    from merchant_intelligence.tasks import enrichment
    return enrichment.candidates()


@router.post("/api/synonyms/propose")
def propose_synonyms():
    """Re-run the WordNet proposal stage (idempotent — statuses preserved).
    400 with an install hint when nltk/wordnet is unavailable."""
    from merchant_intelligence.tasks import enrichment
    r = enrichment.propose_candidates()
    if not r.get("ok"):
        hint = (r.get("wordnet") or {}).get("hint")
        raise HTTPException(
            status_code=400,
            detail=(r.get("reason") or "proposal failed")
                   + (f" — {hint}" if hint else ""))
    return r


@router.post("/api/synonyms/status")
def synonym_status(req: SynonymStatusRequest):
    """The curation gate: mark candidate ids approved or rejected."""
    from merchant_intelligence.tasks import enrichment
    r = enrichment.set_status(req.ids, req.status)
    if not r.get("ok"):
        raise HTTPException(status_code=400,
                            detail=r.get("reason", "bad request"))
    return r


@router.post("/api/synonyms/apply")
def apply_synonyms(req: SynonymApplyRequest):
    """Merge approved candidates into intents.json (weight-2 patterns),
    regenerating vocab.py's defaults in lockstep, appending the phrases to
    the Tier-2 exemplars and recording provenance. Hot-reloaded."""
    from merchant_intelligence.tasks import enrichment
    return enrichment.apply_approved(req.ids)


@router.get("/api/synonyms/manifest")
def synonym_manifest():
    """Provenance of every applied auto-pattern (data/auto_pattern_manifest.json)."""
    from merchant_intelligence.tasks import enrichment
    return enrichment.manifest()


@router.get("/api/shadow/review")
def shadow_review(band: str = "all", limit: int = 100):
    """Phase-1 spot-check tool (design doc §7): shadow decisions joined with
    review labels, band-filtered, plus per-intent precision on the
    high-confidence would-act band — the band clarification labels never
    cover. This is the evidence the Phase 2 go/no-go needs."""
    from merchant_intelligence import calibration
    from merchant_intelligence.tasks import semantic
    if band not in ("all", "would_act", "would_not"):
        raise HTTPException(status_code=400,
                            detail="band must be all|would_act|would_not")
    out = semantic.review(band=band, limit=max(1, min(500, int(limit))))
    # Phase 3: per-intent fitted gates (the auto-run band's accept/override
    # evidence) so the panel can show learning progress in the same view.
    out["tier2_fit"] = calibration.fit_tier2()
    # Shadow-log health (band-independent): lets the Rule Engine chip show
    # today's entry count without opening this panel.
    out["health"] = semantic.shadow_health()
    return out


@router.post("/api/shadow/review")
def shadow_review_label(req: ShadowReviewLabelRequest):
    """Record a reviewer's verdict on one shadow entry (latest wins)."""
    from merchant_intelligence.tasks import semantic
    eid = (req.entry_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail="entry_id is required")
    known = {semantic.entry_id(e) for e in semantic.read_shadow()}
    if eid not in known:
        raise HTTPException(status_code=400,
                            detail="entry_id not found in the shadow log")
    return semantic.label_entry(eid, req.correct, note=req.note,
                                intent=req.intent)


@router.get("/api/audit")
def audit_endpoint(limit: int = 200, action: str = "", actor: str = ""):
    """Immutable audit trail (docs/technical-review-2026-08-original.md #1).

    Newest-first entries for every search, profile view, export, and intent
    execution, plus per-action stats. Append-only by construction — there is
    no update/delete path anywhere in merchant_intelligence/audit.py.
    """
    from merchant_intelligence import audit
    return {
        "ok": True,
        "entries": audit.recent(limit=max(1, min(1000, int(limit))),
                                action=action or None, actor=actor or None),
        "stats": audit.stats(),
        "file": str(audit._path()),
    }


@router.get("/api/ingest")
def ingest_endpoint(limit: int = 20):
    """Ingestion-run ledger + data-freshness signal (governed data platform
    slice, docs/technical-review-2026-08-original.md #2).

    Returns the most recent rebuild runs (append-only, stored in a dedicated
    data/ingest_ledger.db that survives rebuilds) plus a freshness summary:
    which Excel source files are NEW or CHANGED since the last good build.
    """
    from merchant_intelligence import ingest_ledger
    return {
        "ok": True,
        "runs": ingest_ledger.recent(limit=max(1, min(200, int(limit)))),
        "stats": ingest_ledger.stats(),
        "freshness": ingest_ledger.freshness(),
        "file": str(ingest_ledger._db_path()),
    }


@router.post("/api/task/analyze")
def task_analyze(req: TaskRequest):
    """Intent-parser debug endpoint (v2): explain WHY a request was routed
    the way it was.

    Returns every detected intent with its score / confidence / matched
    patterns, the extracted parameters (segment, names, state filter,
    presence filters, limit), and the resulting task descriptor. Use it to
    debug a mis-routed request or to render a "how the parser read this"
    panel in the UI.
    """
    from merchant_intelligence import tasks
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    _audit("task_analyze", json.dumps({"text": text[:300]}))
    try:
        return tasks.analyze(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/calibration")
def get_calibration():
    """Confidence calibration status (feature: fit ask thresholds from usage).

    Returns the logged decision history summary (samples, acceptance by
    confidence band and per intent) plus the fitted thresholds that
    suggest_clarification is currently using — so the Rule Engine UI can
    show how the engine is learning to flag low-confidence requests.
    """
    from merchant_intelligence import calibration
    return {
        "stats": calibration.stats(),
        "fit": calibration.fit(),
        "params": calibration.params(),
        # Phase 3 (design doc §7): per-intent Tier-2 gates fitted from the
        # shadow-review labels — the auto-run band's accept/override evidence.
        "tier2": calibration.fit_tier2(),
    }


@router.post("/api/calibration/reset")
def reset_calibration():
    """Wipe the decision log ("start learning fresh")."""
    from merchant_intelligence import calibration
    removed = calibration.reset()
    return {"ok": True, "removed": removed,
            "stats": calibration.stats()}


@router.get("/api/preferences")
def get_preferences():
    """Saved clarification choices (phrase -> intent) for the Rule Engine UI.

    Each entry is a normalized request phrase the user answered and asked to
    remember ("account details" -> static_account). The UI lists them so a
    wrong or stale memory can be deleted.
    """
    from merchant_intelligence import preferences
    from merchant_intelligence.tasks.engine import CLARIFY_OPTIONS
    prefs = preferences.all_prefs()
    return {
        "count": len(prefs),
        "preferences": [
            {"key": k, "intent": v,
             "label": CLARIFY_OPTIONS.get(v, (v, ""))[0]}
            for k, v in sorted(prefs.items())
        ],
    }


@router.post("/api/preferences/forget")
def forget_preference(req: PreferenceForgetRequest):
    """Forget one saved interpretation (the Rule Engine delete button)."""
    from merchant_intelligence import preferences
    removed = preferences.forget((req.key or "").strip())
    return {"ok": True, "removed": removed,
            "preferences": get_preferences()}


@router.get("/api/intents")
def get_intents():
    """Intent config for the Rule Engine tuning UI (read-only).

    Returns the loaded intents.json (patterns + keywords + _help), where it
    is loaded from, which intents have pipelines registered, and the built-in
    defaults so the UI can offer a one-click "restore defaults".
    """
    from merchant_intelligence.tasks import vocab
    from merchant_intelligence.tasks.pipelines import _PIPELINES
    data = vocab.get_intent_config()
    return {
        "source": vocab.intents_source(),
        "help": data.get("_help", ""),
        "intents": data.get("intents", {}),
        "defaults": vocab.default_intent_specs(),
        "pipelines": sorted(_PIPELINES),
        "name_capable": sorted(vocab.NAME_CAPABLE_INTENTS),
        "chainable": vocab.CHAINABLE,
    }


@router.put("/api/intents")
def update_intent(req: IntentUpdateRequest):
    """Update one intent's patterns/keywords, persist to intents.json, and
    hot-reload the engine so the change applies immediately (no restart)."""
    from merchant_intelligence.tasks import vocab
    intent = (req.intent or "").strip().lower()
    data = vocab.get_intent_config()
    if intent not in (data.get("intents") or {}):
        raise HTTPException(status_code=404, detail=f"unknown intent: {intent}")
    spec = {
        "patterns": [{"pattern": p.pattern, "weight": p.weight} for p in req.patterns],
        "keywords": [k.strip() for k in req.keywords if k.strip()],
    }
    # Preserve the fuzzy toggle through a save: the UI always sends it (the
    # editor's toggle), but a partial client must not silently flip it off.
    if req.fuzzy is not None:
        spec["fuzzy"] = req.fuzzy
    else:
        cur = (data.get("intents") or {}).get(intent)
        if isinstance(cur, dict) and isinstance(cur.get("fuzzy"), bool):
            spec["fuzzy"] = cur["fuzzy"]
    errors = vocab.validate_intent_spec(spec)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    try:
        vocab.save_intent_config(intent, spec)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write config: {exc}")
    return {
        "ok": True,
        "intent": intent,
        "hot_reloaded": True,
        "intents": vocab.get_intent_config().get("intents", {}).get(intent),
    }


@router.get("/api/settings")
def get_settings():
    """Runtime engine settings for the Rule Engine tuning UI (read-only).

    Returns every tunable knob's resolved value, its built-in default, the
    precedence source (env var > data/engine_settings.json > default) and
    the valid range — so the UI can render a proper tuning control.
    """
    from merchant_intelligence import settings as engine_settings
    return {
        "ok": True,
        "settings": engine_settings.all_settings(),
        "file": str(engine_settings._path()),
    }


@router.put("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    """Update runtime engine settings, persist to data/engine_settings.json
    and apply immediately (no restart).

    Settings are read on every use (profile.py reads the threshold per
    build), so a save here is hot-reloaded the moment the next request runs.
    Only the knobs supplied are changed; missing fields keep their value.
    """
    from merchant_intelligence import settings as engine_settings
    current = engine_settings.load()
    if req.decisive_match_threshold is not None:
        current["decisive_match_threshold"] = req.decisive_match_threshold
    if req.semantic_tier_mode is not None:
        if req.semantic_tier_mode not in ("off", "shadow", "enabled"):
            raise HTTPException(
                status_code=400,
                detail="semantic_tier_mode must be one of: off, shadow, enabled")
        current["semantic_tier_mode"] = req.semantic_tier_mode
    engine_settings.save(current)
    return {
        "ok": True,
        "hot_reloaded": True,
        "settings": engine_settings.all_settings(),
        "file": str(engine_settings._path()),
    }


@router.delete("/api/settings")
def reset_settings():
    """Delete data/engine_settings.json so every knob falls back to its
    built-in default (the 'Reset to defaults' button on the Rule Engine)."""
    from merchant_intelligence import settings as engine_settings
    try:
        path = engine_settings._path()
        if path.exists():
            path.unlink()
    except OSError:
        pass
    return {
        "ok": True,
        "settings": engine_settings.all_settings(),
        "file": str(engine_settings._path()),
    }
