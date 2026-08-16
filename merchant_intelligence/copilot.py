"""
copilot.py — Agentic "Merchant Copilot" (technical-review roadmap #4, slice 1).

Turns a COMPOUND natural-language investigation request ("find MEDPLUS, then
get the tids for the above merchant, then the static account and beneficiary")
into an ordered, inspectable, re-runnable plan of steps, then executes every
step through the DETERMINISTIC engine (task pipelines / search).

Hybrid NLU per the design doc ("the LLM is fallible; the rule engine is not"):

  - LLM mode (when LLM_API_KEY is configured): the model proposes the
    decomposition as a list of plain-language sub-requests. Each proposal is
    validated and executed by detect_task/execute_task — the LLM can never
    inject identifiers or bypass a pipeline, because entity extraction always
    goes through the DB-grounded parser.
  - Deterministic mode (no key, or LLM unusable): whole-text detect_task first
    (most requests are already ONE coherent task — clause-attached compounds
    like "email for 2103O338 and phone for MX141692" are handled inside the
    engine); otherwise split_clauses() -> each clause classified as task or
    search.

Chaining: after every executed step, remember_entities() records what it
resolved, so a later step saying "the above merchant" / "then get the tids"
resolves against the PREVIOUS step's output via inherit_reference — the
same follow-up context the /api/task endpoint uses.

Trace: run_copilot returns the plan + per-step results + provenance (mode,
model, elapsed) — the recorded, replayable investigation trace the review
calls for. Every run is audit-logged by the API layer.

Env vars (same as brief.py / engine._llm_interpret):
  LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT
"""

import json
import logging
import os
import re
import time
import urllib.request

from . import config
from .tasks import (
    MAX_INPUT_CHARS,
    detect_task,
    execute_task,
    inherit_reference,
    parse_identifiers,
    remember_entities,
    split_clauses,
)

logger = logging.getLogger(__name__)

# Safety rails: a compound request can never fan out unboundedly, and each
# step's result rows are truncated so the trace stays a reasonable payload.
MAX_STEPS = 8
MAX_ROWS_PER_STEP = 25
MAX_SEARCH_ROWS = 10

_LLM_KEY = os.environ.get("LLM_API_KEY", "")
_LLM_BASE = os.environ.get(
    "LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
_LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))

# Words that carry no entity of their own — a step made only of these is
# garbage the LLM proposed and gets dropped (never executed).
_STOP_TOKENS = {
    "the", "then", "and", "for", "of", "to", "get", "me", "all", "its",
    "their", "them", "please", "pls", "with", "use", "using", "do", "it",
    "this", "that", "also", "after", "now", "so", "can", "you", "i", "we",
    "need", "want", "find", "show", "give", "list", "pull", "check",
    "assist", "help", "please", "kindly", "the", "above", "previous",
}


def llm_configured() -> bool:
    """True when an OpenAI-compatible endpoint is configured."""
    return bool(_LLM_KEY)


# ── Decomposition ─────────────────────────────────────────────────────────

def _plausible_step(text: str) -> bool:
    """Is a proposed sub-request something the engine can act on?

    A step must carry at least one real content token (identifier, merchant
    fragment, field word) — "then do it" is dropped, "find MEDPLUS" / "get
    the tids for the above merchant" / "all NNPC stations" are kept. This is
    the LLM-fabrication guard: nothing the model invents is ever executed
    unless it parses into a real sub-request.
    """
    t = (text or "").strip()
    if not t:
        return False
    tokens = t.split()
    if len(tokens) < 2:
        return False
    return any(
        re.search(r"[A-Z0-9]", w.upper()) and w.lower() not in _STOP_TOKENS
        for w in tokens
    )


def _llm_decompose(text: str) -> list:
    """Ask the LLM for a step decomposition. Returns [] on any failure —
    the caller falls back to the deterministic split, never to nothing."""
    prompt = (
        "You decompose a merchant-registry investigation request into "
        "sequential, self-contained steps. Return ONLY a JSON object:\n"
        '{"steps": [{"text": "<one sub-request>"}]}\n'
        "Rules:\n"
        "- Each step text is a plain-language request the engine can run: "
        "a search by merchant name or identifier, or an instruction like "
        "'get the static account and beneficiary for MX123'.\n"
        "- Keep every original identifier and merchant name in the step "
        "that uses it. Do not invent identifiers, names, or details.\n"
        "- 'the above merchant' / 'the previous request' stays as that "
        "reference phrase — it resolves against the earlier step.\n"
        "- At most 8 steps. If the request is already a single step, return "
        "exactly one.\n\nREQUEST:\n" + text[:4000]
    )
    try:
        req = urllib.request.Request(
            f"{_LLM_BASE}/chat/completions",
            data=json.dumps({
                "model": _LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_LLM_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = (body.get("choices") or [{}])[0].get(
            "message", {}).get("content", "")
        m = re.search(r"\{.*\}", content or "", re.S)
        if not m:
            return []
        data = json.loads(m.group(0))
        steps = []
        for s in (data.get("steps") or [])[:MAX_STEPS]:
            t = str((s or {}).get("text") or "").strip()
            if t and _plausible_step(t) and t not in steps:
                steps.append(t)
        return steps
    except Exception as exc:  # noqa: BLE001 — never let the LLM break a run
        logger.warning("copilot LLM decomposition failed: %s", exc)
        return []


def decompose(text: str, use_llm: bool = True) -> dict:
    """Split a request into an ordered plan of steps.

    Returns:
      {
        "mode": "deterministic" | "llm",
        "steps": [{"text": str, "source": "whole" | "clause" | "llm"}],
        "llm_error": str | None,   # set when LLM was attempted but unusable
        "model": str | None,
      }

    Raises ValueError when the text exceeds MAX_INPUT_CHARS (same contract
    as detect_task — the API layer converts this to a 400).
    """
    t = (text or "").strip()
    if not t:
        return {"mode": "deterministic", "steps": [], "llm_error": None,
                "model": None}
    if len(t) > MAX_INPUT_CHARS:
        raise ValueError(
            f"Input too large: {len(t):,} characters (max {MAX_INPUT_CHARS:,}). "
            "Split the paste into smaller batches."
        )
    # 1) Whole-text task: most requests are already ONE coherent task. The
    #    engine handles clause attachments ('email for A and phone for B')
    #    and chained workflows ('tids then static account') internally, so a
    #    confident single-task parse is a single step. Referential requests
    #    ('the above merchant') are the exception — they NEED the earlier
    #    steps to have run, so they always decompose.
    try:
        whole = detect_task(t, use_llm=False)
    except ValueError:
        raise
    if (whole and whole.get("intent") != "resolve"
            and not whole.get("references_previous")):
        return {
            "mode": "deterministic",
            "steps": [{"text": t, "source": "whole"}],
            "llm_error": None,
            "model": None,
        }
    # 2) LLM proposes the decomposition (validated below; fallback on failure).
    llm_error = None
    if use_llm and llm_configured():
        proposed = _llm_decompose(t)
        if proposed:
            return {
                "mode": "llm",
                "steps": [{"text": s, "source": "llm"} for s in proposed],
                "llm_error": None,
                "model": _LLM_MODEL,
            }
        llm_error = "LLM decomposition unusable — used the rule engine"
    # 3) Deterministic clause split: 'find MEDPLUS then get the tids for the
    #    above merchant' -> two steps, chained through follow-up context.
    clauses = split_clauses(t)
    steps = [{"text": c, "source": "clause"} for c in clauses][:MAX_STEPS]
    return {
        "mode": "deterministic",
        "steps": steps,
        "llm_error": llm_error,
        "model": None,
    }


# ── Step execution ────────────────────────────────────────────────────────

# Pronoun references ("for those", "their tids", "these merchants") are
# common in chained requests but the engine's reference markers are only
# "the above / the previous / per above". Normalization maps them onto the
# marker so a later step resolves against the PREVIOUS step's output — the
# "find MEDPLUS then the static account for those" chain works end-to-end.
_REF_PRONOUN_RE = re.compile(r"\b(?:those|them|these)\b", re.IGNORECASE)


def _normalize_reference(text: str) -> str:
    """Rewrite pronoun references to the engine's reference marker.

    Only fires when the step carries NO entity of its own (no identifiers,
    no merchant name) — a step like "the emails for those MEDPLUS branches"
    keeps its real name and is never rewritten.
    """
    t = (text or "").strip()
    if not _REF_PRONOUN_RE.search(t):
        return t
    try:
        ids = parse_identifiers(t)
    except Exception:  # noqa: BLE001
        ids = {}
    if any(ids.values()):
        return t
    return _REF_PRONOUN_RE.sub("the above merchant", t)


def _try_detect(text: str):
    """detect_task with pronoun-reference recovery: original text, then the
    pronoun-normalized form, then with an instruction verb prefixed (a
    verb-less referential step "the static account for those" needs a verb
    for the engine's referential branch to fire). Returns None when nothing
    parses — the caller falls back to a search step."""
    try:
        d = detect_task(text, use_llm=False)
    except ValueError:
        d = None
    if d:
        return d
    norm = _normalize_reference(text)
    if norm != text:
        try:
            d = detect_task(norm, use_llm=False)
        except ValueError:
            d = None
        if d:
            return d
        try:
            d = detect_task("get " + norm, use_llm=False)
        except ValueError:
            d = None
        if d:
            return d
    return None


def _task_step(text: str, index: int) -> dict:
    """Run one sub-request through the deterministic task engine."""
    detected = _try_detect(text)
    if not detected:
        return _search_step(text, index)
    own_entity = ((detected.get("identifier_count") or 0) > 0
                  or bool(detected.get("names"))
                  or bool(detected.get("segment")))
    if detected.get("references_previous") and not own_entity:
        inherit_reference(detected)
    result = execute_task(detected)
    rows = (result.get("rows") or [])[:MAX_ROWS_PER_STEP]
    not_found = (result.get("not_found") or [])[:MAX_ROWS_PER_STEP]
    return {
        "index": index,
        "text": text,
        "kind": "task",
        "source": "engine",
        "intent": detected.get("intent"),
        "intents": detected.get("intents") or [],
        "identifiers": detected.get("identifiers") or {},
        "names": detected.get("names") or [],
        "context_inherited": bool(detected.get("context_inherited")),
        "rows": len(result.get("rows") or []),
        "not_found": len(result.get("not_found") or []),
        "columns": result.get("columns") or [],
        "workflow_executed": result.get("workflow_executed") or [],
        "summary": result.get("summary") or "",
        "result": {"intent": result.get("intent"),
                   "columns": result.get("columns") or [],
                   "rows": rows, "not_found": not_found,
                   "summary": result.get("summary") or ""},
    }


def _search_step(text: str, index: int) -> dict:
    """Fallback: a sub-request that isn't a task runs as a registry search."""
    from .search import MerchantSearch
    try:
        res = MerchantSearch().search(text, limit=MAX_SEARCH_ROWS, min_score=0)
    except Exception as exc:  # noqa: BLE001 — honest empty result on failure
        logger.warning("copilot search step failed: %s", exc)
        res = []
    rows = [r.to_dict() for r in res][:MAX_ROWS_PER_STEP]
    top = rows[0] if rows else {}
    ids: dict = {}
    for k in ("tid", "mxcode", "phone", "email"):
        v = top.get(k)
        if v:
            ids.setdefault(k, []).append(str(v))
    return {
        "index": index,
        "text": text,
        "kind": "search",
        "source": "engine",
        "intent": "search",
        "identifiers": {},
        "names": [],
        "context_inherited": False,
        "rows": len(rows),
        "not_found": 0,
        "columns": ["Merchant Name", "TID", "MX Code", "Email", "Phone",
                    "Match Type", "Score", "Sheet"],
        "workflow_executed": [],
        "summary": f"{len(rows)} search result(s)",
        "result": {"query": text, "count": len(rows), "rows": rows},
        "top_entity": {"name": top.get("merchant_name") or "",
                       "identifiers": ids},
    }


def _run_step(text: str, index: int) -> dict:
    """Execute one plan step. Tasks go through the pipelines; anything the
    engine won't parse as a task runs as a search. Never raises."""
    step = _task_step(text, index)
    # Chain: remember what this step resolved so a later step's 'the above
    # merchant' resolves against IT, not a stale earlier request.
    try:
        if step["kind"] == "task":
            remember_entities(step.get("identifiers"),
                              step.get("names") or None)
        else:
            top = step.get("top_entity") or {}
            remember_entities(top.get("identifiers"),
                              [top["name"]] if top.get("name") else None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot chaining failed: %s", exc)
    return step


# ── Public API ────────────────────────────────────────────────────────────

def run_copilot(text: str, use_llm: bool = True) -> dict:
    """Decompose a compound request and execute every step deterministically.

    Returns the full trace:
      {
        "ok": True,
        "text": str,
        "mode": "deterministic" | "llm",
        "model": str | None,
        "llm_error": str | None,
        "plan": [{"index", "text", "source"}],         # the re-runnable plan
        "steps": [step...],                            # per-step results
        "summary": str,
        "elapsed_ms": float,
      }
    """
    t0 = time.perf_counter()
    plan = decompose(text, use_llm=use_llm)
    steps = [_run_step(s["text"], i)
             for i, s in enumerate(plan["steps"], start=1)]
    summary = _build_summary(steps)
    return {
        "ok": True,
        "text": text,
        "mode": plan["mode"],
        "model": plan["model"],
        "llm_error": plan["llm_error"],
        "plan": [{"index": i, "text": s["text"], "source": s["source"]}
                 for i, s in enumerate(plan["steps"], start=1)],
        "steps": steps,
        "summary": summary,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _build_summary(steps: list) -> str:
    """One-line human summary of the executed trace."""
    if not steps:
        return "Nothing to execute — the request parsed into no steps."
    parts = []
    for s in steps:
        if s["kind"] == "task":
            parts.append(f"step {s['index']}: {s['intent']} "
                         f"({s['rows']} row(s))")
        else:
            parts.append(f"step {s['index']}: search ({s['rows']} result(s))")
    return " → ".join(parts)
