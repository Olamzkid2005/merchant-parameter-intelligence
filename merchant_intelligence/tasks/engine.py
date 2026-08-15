"""
engine.py — Task orchestration for the task engine.

detect_task (text -> task descriptor with intent / identifiers / names /
segment / params), the LLM refinement helpers (_llm_configured /
_llm_interpret), execute_task (run the intent pipelines and merge the tables)
and analyze (public debug breakdown).
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .. import settings as engine_settings
from ..calibration import params as calibration_params
from ..preferences import lookup as preference_lookup
from .db import _connect
from .intents import (
    _analyze, _countable_target, _detect_negated_intents,
    build_execution_plan, detect_intents, extract_clause_entities,
)
from .models import PipelineResult, TaskDescriptor
from .parser import (
    _key_root_boundary, _line_has_instruction, _looks_like_segment,
    _match_key_merchant, extract_compare_pair, extract_names, extract_params,
    extract_reference, extract_segment, key_merchant_matches,
    looks_like_address, parse_identifiers, parse_named_identifiers,
)
from .pipelines import (
    _PIPELINES, _merge_tables, _pipeline_resolve, extract_produced_values,
)
from .vocab import (
    CHAINABLE, FIELD_NAME_STOPS, ID_KINDS, INTENT_GRAPH, INTENT_KEYWORDS,
    MAX_INPUT_CHARS, NAME_CAPABLE_INTENTS, NIGERIA_STATES, SEGMENT_EXTRA_STOP,
    WORKFLOW_STEPS, _lower,
)

logger = logging.getLogger(__name__)

# Field-extraction intents: "one fragment -> one answer column" requests.
# Name-only versions without an instruction verb ("what state is lagoons in")
# become tasks through a dedicated branch gated on confidence, so a plain
# name like "BANK OF INDUSTRY" (weak bank signal) stays a normal search.
FIELD_EXTRACT_INTENTS = {
    "email", "phone", "mxcode", "tid", "address", "bank", "account_name",
    "account_number", "payable", "alias", "contact", "onboarded", "state",
    "source", "beneficiary",
}


def detect_task(
    text: str, use_llm: bool = True,
    intent_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a task descriptor if the text reads as a request, else None.

    v2: weighted intents + confidence. Rules stay conservative — a plain
    merchant name must remain a normal search:
      - multi-line AND (>=1 identifier OR instruction words)      -> task
      - >=2 identifier tokens (any line layout)                    -> task
      - single line, instruction verbs AND an identifier          -> task
      - single line, identifier AND a clear intent (no verb)      -> task
      - name-capable intent + instruction verb + merchant name    -> task
      - segment / count / duplicates / summary phrases            -> task

    LLM refinement (feature #8) fires when the top intent is ambiguous
    ('resolve') OR its confidence is low (< 45) and the text reads as an
    instruction — so unclear phrasing gets help, not a blind guess.

    intent_override: an explicit interpretation choice (the user picked an
    option from a clarification prompt, e.g. 'static_account' for "account
    details"). Forces exactly that intent — never an excluded one — so the
    clarification round-trip re-runs the chosen pipeline.

    Raises ValueError when the text exceeds MAX_INPUT_CHARS — the identifier
    classifier probes the registry per token, so an unbounded paste would
    hammer the DB. API callers convert this to a 400; script callers should
    check len(text) against MAX_INPUT_CHARS first.
    """
    t = (text or "").strip()
    if not t:
        return None
    if len(t) > MAX_INPUT_CHARS:
        raise ValueError(
            f"Input too large: {len(t):,} characters (max {MAX_INPUT_CHARS:,}). "
            "Split the paste into smaller batches."
        )
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    idents = parse_identifiers(t)
    n_ids = sum(len(v) for v in idents.values())
    low = _lower(t)
    # Referential phrasing ("the above merchant", "the previous request"):
    # the entity comes from a PREVIOUS request — the wording itself must never
    # be read as a merchant name ("ABOVE MERCHANT" is garbage).
    ref = extract_reference(t)
    # Whole-word instruction detection (same rule as extract_names): a bare
    # substring check would read "RELIABLE PHONES AND GADGET" as an
    # instruction ("get" inside "GADGET") and misroute a plain merchant
    # name into a phone task.
    has_instr = _line_has_instruction(t)
    is_multiline = len(lines) > 1
    excluded = _detect_negated_intents(t)
    intents = detect_intents(t, exclude=excluded)
    # Pasted name lists: field words inside merchant names ("RELIABLE PHONES
    # AND GADGET") are DATA, not requests — they must never add intents.
    # When a multiline name-only paste carries an instruction line, only the
    # intents that line itself expresses survive ('pls get the merchant
    # code for these merchants\nRELIABLE PHONES AND GADGET' -> mxcode, not
    # mxcode+phone). Requests without an instruction line are untouched.
    if is_multiline and n_ids == 0 and has_instr:
        instr_lines = [ln for ln in lines if _line_has_instruction(ln)]
        if instr_lines:
            instr_intents = [
                i for i in detect_intents("\n".join(instr_lines),
                                          exclude=excluded)
                if i != "resolve"
            ]
            if instr_intents:
                intents = [i for i in intents if i in instr_intents] \
                    or instr_intents
    analysis = _analyze(t, exclude=excluded)
    top_conf = (analysis.get(intents[0], {}).get("confidence", 0)
                if intents[0] != "resolve" else 0)
    # Collection requests ("all the addresses of all nnpc stations") carry no
    # identifiers — the segment IS the input. Injected only when no
    # per-merchant intent (profile) or count was explicitly requested.
    if (n_ids == 0 and _looks_like_segment(t)
            and "segment" not in intents
            and "profile" not in intents and "count" not in intents):
        intents = ["segment"] + [i for i in intents
                                  if i not in ("email", "phone", "mxcode",
                                               "tid", "address", "bank",
                                               "account_name",
                                               "account_number", "payable",
                                               "alias", "contact",
                                               "onboarded", "state",
                                               "source", "beneficiary",
                                               "top", "profile", "resolve")]
        top_conf = 90  # injected from a collective-marker + field-word phrase

    is_task = False
    # Multi-line blocks are only tasks when they carry identifiers — a multi-
    # line paste of plain merchant names must stay a normal search.
    if is_multiline and n_ids >= 1:
        is_task = True
    elif n_ids >= 2:
        is_task = True
    elif n_ids >= 1 and has_instr:
        is_task = True
    # Single line with an identifier AND a specific intent ("MX141692 alias
    # payables static account" — no verb needed). A bare "MX141692" stays
    # resolve -> search.
    elif n_ids >= 1 and intents[0] != "resolve":
        is_task = True
    elif (has_instr and n_ids == 0
          and any(i in NAME_CAPABLE_INTENTS for i in intents)
          and extract_names(t)):
        # Name-only natural-language request: "get me all the information on
        # medplus" (profile intent + a merchant name, no identifiers).
        is_task = True
    elif (n_ids == 0 and any(i in FIELD_EXTRACT_INTENTS for i in intents)
          and top_conf >= 40 and extract_names(t)
          and any(w in low for w in ("what", "which", "who", "when",
                                     "where", "how", "list", "check"))):
        # Verb-less name-only field request: "what state is lagoons in",
        # "list all aliases for lagoons". Confidence-gated AND question-word
        # gated so a plain name like "BANK OF INDUSTRY" (weak bank 3) or
        # "LAGOON WATERS ADDRESS" (a name, not a request) never misroutes
        # into a task.
        is_task = True
    elif (n_ids == 0
          and any(i in (FIELD_EXTRACT_INTENTS | {"profile"}) for i in intents)
          and top_conf >= 40
          and any(_match_key_merchant(n) for n in extract_names(t))):
        # Key-merchant shorthand: "medplus emails", "addide addresses",
        # "spar phone number", "medplus full profile" — a known merchant
        # root + a field word is a task even without an instruction verb or
        # question word. The key-merchant gate keeps a generic "LAGOON
        # WATERS ADDRESS" (a name, not a request) as a normal search, and
        # MEDPLUS's many entries (MEDPLUS LIMITED / MEDPLUS PHARMACY /
        # branches) all resolve through the root prefix match. Typo variants
        # ("medpluz emails") still count — key_merchant_matches is within
        # one edit of the root.
        is_task = True
    elif (intents[0] == "change_details" and n_ids == 0
          and extract_names(t)):
        # "change of account details for X" — the change phrase IS the request.
        is_task = True
    elif "segment" in intents and n_ids == 0:
        is_task = True
    elif intents[0] == "count" and n_ids == 0 and _countable_target(t):
        # "how many nnpc merchants" / "count all nnpc merchants" — but never
        # a plain name like "COUNT OF MONTE CRISTO".
        is_task = True
    elif intents[0] == "duplicates" and (has_instr or n_ids >= 1
                                         or bool(extract_segment(t)[0])):
        # "find duplicate merchants in the NNPC file".
        is_task = True
    elif intents[0] == "summary" and (has_instr or n_ids >= 1
                                      or bool(extract_segment(t)[0])):
        # "summarize the NNPC file" / "give me a summary of the MRSP file".
        is_task = True
    elif intents[0] == "compare" and (len(extract_compare_pair(t)) == 2
                                      or n_ids >= 2):
        # "compare LAGOON WATERS vs ARTEE INDUSTRIES" — two sides needed.
        is_task = True
    elif intents[0] == "coverage" and bool(extract_segment(t)[0]):
        # "which nnpc stations have no email" — a segment + missing filter.
        is_task = True
    elif intents[0] == "top" and (has_instr or bool(extract_segment(t)[0])):
        # "top 10 banks in the NNPC file" / "how many merchants per state".
        is_task = True
    elif intents[0] == "verify" and (n_ids >= 1 or extract_names(t)):
        # "is 2103O338 in the registry" / "is lagoons registered".
        is_task = True
    elif (ref and has_instr and n_ids == 0
          and any(i in (FIELD_EXTRACT_INTENTS | NAME_CAPABLE_INTENTS)
                  for i in intents)):
        # Referential follow-up: "get the tids for the above merchant" — the
        # merchant comes from the previous request, not this text. Never
        # extract a name from the reference wording.
        is_task = True
    elif intents[0] == "related" and extract_names(t):
        # Name-only relationship request: "who else is linked to lagoons".
        is_task = True
    elif intents[0] == "formerly" and extract_names(t):
        # "what was just chips formerly called" (no instruction verb needed).
        is_task = True
    elif (intents[0] == "static_account" and has_instr and n_ids == 0
          and not extract_names(t)):
        # Template phrasing with no merchant filled in ("Please retrieve this
        # merchant's MXCODE…" with nothing after it) — run the pipeline so it
        # can say 'no merchant found in the request' instead of dead-ending
        # into a normal search.
        is_task = True

    if not is_task:
        return None

    # Names are only meaningful for NAME-ONLY requests. When identifiers are
    # present, the identifiers ARE the input — extracting a "name" out of the
    # request wording would send gibberish through the search engine. Named
    # pairs ('2103O338 FELIX OKONMAH') are kept separately in task.named.
    # Under a reference ('the above merchant') the wording carries NO merchant
    # name of its own — names stay empty so 'ABOVE MERCHANT' never becomes one.
    task_names = [] if (n_ids or ref) else (
        extract_compare_pair(t) if intents[0] == "compare"
        else extract_names(t))
    # Field-intent name cleanup: "get medplus phone and email" extracts the
    # field word into the name (PHONE is not a NAME_STOP_WORD because
    # "RELIABLE PHONES AND GADGET" is a real pasted-list merchant). For a
    # SINGLE-LINE name-only field request, strip only TRAILING field-vocab
    # words from the extracted name so the pipeline searches "MEDPLUS", not
    # "MEDPLUS PHONE" — while a merchant whose name CONTAINS the field word
    # in the middle ("get the phone for SUN PHONE STORE" -> "SUN PHONE
    # STORE") keeps its full name. Multiline pastes and compound identifier
    # requests are never touched.
    if (task_names and not is_multiline and n_ids == 0 and not ref
            and any(i in FIELD_NAME_STOPS for i in intents)):
        stops = set()
        for i in intents:
            stops |= FIELD_NAME_STOPS.get(i, set())
        cleaned = []
        for n in task_names:
            words = n.split()
            while words and words[-1] in stops:
                words.pop()
            if words:
                cleaned.append(" ".join(words))
        task_names = cleaned
    # Key-merchant canonicalisation: a typo'd request ("medpluz emails") may
    # keep the misspelt name after the trailing-strip above. Rewrite it to
    # the matched key root ("MEDPLUS") so the pipeline searches the REAL
    # merchant family — and record which roots matched for the UI (Rule
    # Engine badge / analyze debug). Prefix-matched names ("SPAR LEKKI") are
    # already canonical and stay untouched.
    key_merchants: List[str] = []
    if task_names and n_ids == 0 and not ref:
        normalized = []
        for n in task_names:
            roots = key_merchant_matches(n)
            if roots:
                root = roots[0]
                if n != root and not _key_root_boundary(n, root):
                    n = root  # typo variant -> canonical root
                if root not in key_merchants:
                    key_merchants.append(root)
            normalized.append(n)
        task_names = normalized
    # Address-shaped pastes ('BRITISH INTERNATIONAL SCHOOL ROAD, LEKKI,
    # LAGOS' under 'get me the tids for …') must be matched against the
    # ADDRESS column — a road + city string has no merchant-name overlap,
    # so fuzzy name search returns unrelated stores. When EVERY extracted
    # name reads as an address, the whole request is an address lookup.
    # Mixed lists (real merchants + one address-like name) stay on the name
    # path so a merchant like 'SWEB MARYLAND MALL' is still searched by
    # name.
    names_are_addresses = bool(task_names) and all(
        looks_like_address(n) for n in task_names)
    task = TaskDescriptor(
        intent=intents[0],
        intents=intents,
        identifiers=idents,
        named=parse_named_identifiers(t),
        names=task_names,
        names_are_addresses=names_are_addresses,
        identifier_count=n_ids,
        has_instruction=has_instr,
        multiline=is_multiline,
        confidence=top_conf,
        analysis=analysis,
        params=extract_params(t),
        raw=t,
        key_merchants=key_merchants,
    )
    # Clause-level attachments: which identifier(s) each intent owns
    # ('email for 2103O338 and phone for MX141692'). Empty for name-only /
    # single-identifier requests — there is nothing to attach. Computed
    # before LLM refinement so an LLM-added identifier simply disables
    # scoping downstream (execute_task's coverage guard), never drops data.
    task.clauses = extract_clause_entities(t, known_ids=idents)

    # Structured log line — every detected task carries its intent, identifier
    # count and confidence as fields so production debugging ("what did the
    # parser do with this paste?") is a grep away.
    logger.info(
        "task_detected intent=%s ids=%d conf=%d len=%d",
        task.intent, task.identifier_count, task.confidence, len(t),
        extra={"intent": task.intent, "identifier_count": task.identifier_count,
               "confidence": task.confidence, "char_len": len(t)},
    )

    # LLM refinement for ambiguous or low-confidence requests (feature #8):
    # resolve intent with instruction words, OR a confident-enough-to-be-a-task
    # phrasing whose top intent is still shaky (< 45) — ask the LLM which
    # intent(s) are meant. The LLM only refines, never replaces the gate.
    needs_llm = (task.intent == "resolve" and has_instr) or \
                (top_conf < 45 and has_instr and task.intent != "resolve")
    if use_llm and needs_llm and _llm_configured():
        llm = _llm_interpret(t)
        if llm:
            task.intent = llm.get("intent", task.intent)
            llm_intents = llm.get("intents") or []
            if llm_intents:
                task.intents = llm_intents
            task.llm_refined = True
            for kind, vals in (llm.get("identifiers") or {}).items():
                if vals and not task.identifiers.get(kind):
                    task.identifiers[kind] = vals
                    task.identifier_count += len(vals)
    # Negation always wins: an intent the user excluded ('...but not the
    # change history') is dropped even if the LLM re-suggested it.
    if excluded:
        task.intents = [i for i in task.intents if i not in excluded]
        if not task.intents:
            task.intents = ["resolve"]
        task.intent = task.intents[0]
    task.excluded = excluded
    task.workflow = build_execution_plan(task.intents)
    if task.intent in ("segment", "count", "duplicates", "summary",
                       "coverage", "top") and n_ids == 0:
        # Segment-style intents carry the fragment + fields, never names — the
        # phrasing ("all the addresses of all NNPC", "how many NNPC") must not
        # leak 'ADDRESSES NNPC' / 'COUNT NNPC' into the name search.
        task.names = []
        task.segment, task.segment_fields = extract_segment(t)
        # Keep only real fragment words: drop the intent's own vocabulary,
        # limit tokens ("top 20"), and the state filter word.
        state = task.params.get("state")
        state_tokens = ({a.upper() for a in NIGERIA_STATES.get(state, [state])}
                        if state else set())
        kept = [w for w in task.segment.split()
                if w not in SEGMENT_EXTRA_STOP and w not in state_tokens
                and not w.isdigit()]
        task.segment = " ".join(kept)
        # Key-merchant canonicalisation for segments too: "all adide stores"
        # (a typo the gate admitted) must search ADDIDE, not ADIDE.
        roots = key_merchant_matches(task.segment)
        if roots:
            task.segment = roots[0]
    # Referential requests flag for the API layer: it resolves "the above
    # merchant" against the last remembered context. Only when the request
    # carries NO entity of its own — a fragment ('NNPC'), a name, or an
    # identifier all count as its own entity.
    task.references_previous = bool(
        ref and task.identifier_count == 0 and not task.names and not task.segment)
    # Explicit interpretation choice (clarification flow): the user picked one
    # option, so force exactly that intent — never an excluded one. The other
    # fields (identifiers, names, params) stay as parsed.
    if intent_override:
        if intent_override in INTENT_KEYWORDS and intent_override not in excluded:
            task.intent = intent_override
            task.intents = [intent_override]
            task.workflow = build_execution_plan(task.intents)
    return task.to_dict()


# ── LLM interpretation (feature #8) ──────────────────────────────────────

# ── Follow-up context (feature: 'the above merchant') ────────────────────
# The task engine itself is stateless (detect_task is pure); the API layer
# remembers the LAST request's entities here so a referential follow-up
# ("get the tids for the above merchant") can be resolved against them. A
# single-user local tool, so one module-level slot is enough.
_last_entities: Dict[str, Any] = {}


def remember_entities(identifiers: Optional[Dict[str, List[str]]] = None,
                      names: Optional[List[str]] = None) -> None:
    """Record the entities of the most recent request as follow-up context.

    Empty context is ignored (never clobbers a good one), so a segment request
    with no merchant keeps the previous merchant as the reference target.
    """
    ids = {k: list(v) for k, v in (identifiers or {}).items() if v}
    nms = [n for n in (names or []) if n]
    if not any(ids.values()) and not nms:
        return
    _last_entities["identifiers"] = ids
    _last_entities["names"] = nms


def last_entities() -> Dict[str, Any]:
    """The remembered follow-up context (read-only view)."""
    return {"identifiers": dict(_last_entities.get("identifiers", {})),
            "names": list(_last_entities.get("names", []))}


# Segment-style intent -> the per-record pipeline that answers the request
# once identifiers/names are inherited ("get me all the tids ... and their
# addresses" -> run the tid + address pipelines over the inherited merchant).
_REF_FIELD_PIPELINE = {
    "address": "address", "email": "email", "phone": "phone",
    "mxcode": "mxcode", "tid": "tid", "contact": "contact",
    "state": "state", "onboarded": "onboarded", "bank": "bank",
    "account": "account_number", "merchant": "profile",
}


def inherit_reference(task: Dict[str, Any]) -> bool:
    """Fill a referential request with the last remembered entities.

    Mutates the task descriptor in place (identifiers/names from the previous
    request) and returns True when context was applied. Collection intents
    (segment/coverage/top/...) can't run per-record, so they switch to the
    field pipelines the request actually asked for (its segment_fields), or
    the full profile when none map — 'get all the tids and addresses for the
    above merchant' over an inherited merchant becomes tid + address rows.
    """
    ctx = _last_entities
    if not ctx:
        return False
    ids = {k: list(v) for k, v in (ctx.get("identifiers") or {}).items() if v}
    names = [n for n in (ctx.get("names") or []) if n]
    if not any(ids.values()) and not names:
        return False
    task["identifiers"] = ids
    task["names"] = names
    task["identifier_count"] = sum(len(v) for v in ids.values())
    task["context_inherited"] = True
    if task.get("intent") in ("segment", "coverage", "top", "count",
                               "duplicates", "summary"):
        fields = [_REF_FIELD_PIPELINE[f] for f in (task.get("segment_fields") or [])
                  if f in _REF_FIELD_PIPELINE]
        fields = list(dict.fromkeys(fields))
        if not fields:
            fields = ["profile"]
        task["intent"] = fields[0]
        task["intents"] = fields
        task["workflow"] = build_execution_plan(fields)
    return True


def _llm_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY", ""))


def _llm_interpret(text: str) -> Optional[Dict[str, Any]]:
    """Ask an OpenAI-compatible endpoint to interpret an ambiguous request.

    Returns {intent, intents, identifiers} or None on any failure (the
    heuristic result is kept — the LLM only ever refines, never replaces).
    """
    key = os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    prompt = (
        "You interpret merchant-registry requests. Given the text below, "
        "return ONLY a JSON object with keys: "
        '"intent" (one of: static_account, mxcode, tid, email, phone, '
        'address, bank, account_name, account_number, payable, alias, '
        'contact, onboarded, state, source, beneficiary, profile, '
        'change_details, related, formerly, compare, coverage, top, verify, '
        'segment, count, duplicates, summary, resolve), '
        '"intents" (array of all intents in the text), and '
        '"identifiers" (object with arrays for tid, mxcode, phone, email, '
        "account, payable, bvn, mid, alias).\n\nTEXT:\n" + text[:4000]
    )
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", content or "", re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        if (data.get("intent") not in INTENT_KEYWORDS
                and data.get("intent") not in ("resolve", "segment")):
            data["intent"] = "resolve"
        return data
    except Exception as exc:
        logger.warning("LLM task interpretation failed: %s", exc)
        return None


# ── DB access ─────────────────────────────────────────────────────────────
def suggest_next_steps(task: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, str]]:
    """Auto-suggest related pipelines not already requested (feature #10).

    Each suggestion carries a ready-made prompt (identifiers + instruction)
    the frontend can re-run with one click.
    """
    intents = set(task.get("intents") or [task.get("intent", "resolve")])
    ids = task.get("identifiers", {})
    id_lines = []
    for kind in ID_KINDS:
        for v in ids.get(kind, []):
            if v not in id_lines:
                id_lines.append(v)
    # Name-only requests have no identifiers — the extracted name is the
    # input, so suggestions re-run against it ("also get the emails for MEDPLUS").
    for n in task.get("names") or []:
        if n not in id_lines:
            id_lines.append(n)
    if not id_lines:
        return []
    base = "\n".join(id_lines)
    out = []
    for intent, (label, suffix) in CHAINABLE.items():
        if intent in intents:
            continue
        out.append({
            "intent": intent,
            "label": label,
            "prompt": f"{base}\n{suffix}",
        })
        if len(out) >= 3:
            break
    return out


def _clause_scope(task: Dict[str, Any],
                   clauses: List[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, List[str]]]]:
    """Per-intent identifier scoping, or None when clauses don't cover the
    whole identifier set.

    'get email for 2103O338 and phone for MX141692' -> email runs on
    2103O338, phone on MX141692. Scoping is only applied when the attached
    identifiers match the FULL identifier set exactly — a leading id-only
    clause, a comma-separated paste, or an LLM-added identifier makes the
    sets differ, and we fall back to the full set rather than drop data.
    """
    if not clauses:
        return None
    full = {str(v).upper().strip()
            for vals in (task.get("identifiers") or {}).values() for v in vals}
    attached = {str(v).upper().strip()
                for c in clauses for vals in c.get("identifiers", {}).values()
                for v in vals}
    if full != attached:
        return None
    return {c["intent"]: c.get("identifiers", {}) for c in clauses}


# ── Workflow execution (feature: execute the dependency-aware plan) ────────
# build_execution_plan() describes the plan (the UI renders the step verbs);
# execute_workflow() runs it: steps execute in dependency order and a step
# whose `requires` names an earlier step consumes that step's produced
# identifier values. A declared requirement whose step is NOT in the plan is
# either satisfied internally by the pipeline itself (static_account resolves
# TIDs/MX to MX codes) or synthesized as a resolve step — a name-only
# "static account for LAGOON WATERS" genuinely runs resolve_mxcode ->
# fetch_static_account, feeding the produced MX codes forward.

# Step verb -> the intent that produces it (inverse of WORKFLOW_STEPS).
_STEP_VERB_INTENT = {v: k for k, v in WORKFLOW_STEPS.items()}

# Requirements each pipeline satisfies from its OWN identifiers — never
# synthesized and never injected (the pipeline resolves them internally).
_REQ_SATISFIED_INTERNALLY = {
    "static_account": {
        "resolve_mxcode": {"tid", "mxcode", "static", "account"},
    },
}


def _plan_steps(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((task.get("workflow") or {}).get("steps") or [])


def _topo_order(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable dependency order: a step runs only after every step it requires
    (by step verb) that is present in the plan. Edges naming an absent step
    are handled by _synthesize_requirements before this runs."""
    by_verb = {s.get("step"): s for s in steps}
    ordered: List[Dict[str, Any]] = []
    done: set = set()
    pending = list(steps)
    while pending:
        progressed = False
        remaining: List[Dict[str, Any]] = []
        for s in pending:
            reqs = [r for r in (s.get("requires") or []) if r in by_verb]
            if all(r in done for r in reqs):
                ordered.append(s)
                done.add(s.get("step"))
                progressed = True
            else:
                remaining.append(s)
        if not progressed:
            # Cycle or orphaned edge — run the rest in declared order.
            ordered.extend(remaining)
            break
        pending = remaining
    return ordered


def _synthesize_requirements(task: Dict[str, Any],
                             steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Insert missing requirement steps so every declared `requires` is
    satisfied by an actual run.

    A requirement whose step is absent from the plan is either satisfied
    internally (the pipeline resolves the value itself — see
    _REQ_SATISFIED_INTERNALLY) or needs a resolve step to produce it. The
    latter is synthesized (name-only requests: "static account for LAGOON
    WATERS" gains a resolve_mxcode step before fetch_static_account).
    Returns the expanded step list; each synthesized step runs once even when
    several steps depend on it.
    """
    have = {s.get("step") for s in steps}
    idents = task.get("identifiers") or {}
    expanded: List[Dict[str, Any]] = []
    inserted: set = set()
    for s in steps:
        needs = []
        for req in s.get("requires") or []:
            if req in have or req in inserted:
                continue
            internal_kinds = (_REQ_SATISFIED_INTERNALLY.get(s.get("intent"), {})
                              .get(req))
            if internal_kinds and any(idents.get(k) for k in internal_kinds):
                # The pipeline satisfies the requirement from its own input
                # (TIDs/MX/accounts given) — nothing to synthesize.
                continue
            if not (task.get("names") or any(idents.values())):
                # Nothing that could feed the requirement (a bare template
                # with no merchant) — leave it unmet so the pipeline's own
                # "no merchant found" message surfaces.
                continue
            producer = _STEP_VERB_INTENT.get(req)
            if producer is None:
                continue  # unknown verb — leave the requirement unmet
            needs.append((req, producer))
        for req, producer in needs:
            expanded.append({
                "intent": producer,
                "step": req,
                "requires": [],
                "resolved_internally": [],
                "produces": list((INTENT_GRAPH.get(producer) or {})
                                  .get("produces", [])),
            })
            inserted.add(req)
        expanded.append(s)
    return expanded


def _inject_produced(task: Dict[str, Any],
                     produced: Dict[str, Dict[str, Any]],
                     step: Dict[str, Any],
                     ran: List[str]) -> Optional[Dict[str, Any]]:
    """Give a step the values upstream steps produced for it.

    Only explicit `requires` edges trigger injection, so a compound that asks
    for unrelated fields (email + phone) is unaffected — each step keeps the
    original identifiers, exactly as before. Returns a shallow-copied task
    with the produced values merged in, or None when nothing applies.
    """
    reqs = [r for r in (step.get("requires") or []) if r in ran]
    if not reqs:
        return None
    ids = {k: list(v) for k, v in (task.get("identifiers") or {}).items()}
    names = list(task.get("names") or [])
    chained: List[str] = []
    added = 0
    for up in reqs:
        vals = produced.get(up) or {}
        for kind, values in (vals.get("identifiers") or {}).items():
            existing = {v.upper() for v in ids.get(kind, [])}
            fresh = [v for v in values if v.upper() not in existing]
            if fresh:
                ids.setdefault(kind, []).extend(fresh)
                added += len(fresh)
                chained.append(up)
        for n in vals.get("names") or []:
            if n.upper() not in {x.upper() for x in names}:
                names.append(n)
                added += 1
                chained.append(up)
    if not added:
        return None
    scoped = dict(task)
    scoped["identifiers"] = ids
    scoped["names"] = names
    scoped["identifier_count"] = task.get("identifier_count", 0) + added
    scoped["_chained_from"] = list(dict.fromkeys(chained))
    return scoped


def execute_workflow(conn, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run the task's dependency-aware plan and merge the step tables.

    Returns a render-ready table (same shape as the legacy merge) with the
    execution trace attached: `workflow_executed` (step verbs in run order,
    including synthesized resolve steps) and `workflow_chain` (step verb ->
    {"from": upstream steps, "values": n} for every step that consumed
    upstream produced identifiers). Returns None when the task carries no
    plan — the caller falls back to the legacy intent loop.
    """
    steps = _synthesize_requirements(task, _plan_steps(task))
    if not steps:
        return None
    ordered = _topo_order(steps)
    intents = [s.get("intent") or "resolve" for s in ordered]
    scope = _clause_scope(task, task.get("clauses") or [])
    # Only scope when EVERY attached clause intent is actually run — if a
    # clause's intent was dropped from the intents list (static_account
    # subsumes mxcode: 'get mxcode for A and static account for B'), its
    # identifiers would vanish from the output. Fall back to the full set
    # so no identifier ever disappears.
    if scope and not set(scope).issubset(intents):
        scope = None
    tables: List[Dict[str, Any]] = []
    produced: Dict[str, Dict[str, Any]] = {}
    chain: Dict[str, Any] = {}
    ran: List[str] = []
    for step in ordered:
        intent = step.get("intent") or "resolve"
        scoped_task = task
        if scope and intent in scope:
            scoped_task = dict(task)
            scoped_task["identifiers"] = scope[intent]
        injected = _inject_produced(scoped_task, produced, step, ran)
        if injected is not None:
            scoped_task = injected
            chain[step.get("step")] = {
                "from": list(dict.fromkeys(
                    injected.get("_chained_from") or [])),
                "values": injected["identifier_count"]
                           - task.get("identifier_count", 0),
            }
        pipeline = _PIPELINES.get(intent, _pipeline_resolve)
        table = pipeline(conn, scoped_task)
        tables.append(table)
        ran.append(step.get("step"))
        produced[step.get("step")] = extract_produced_values(
            table, step.get("produces") or [])
    merged = _merge_tables(tables, intents)
    merged["workflow_executed"] = [s.get("step") for s in ordered]
    merged["workflow_chain"] = chain
    return merged


def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run the task pipeline(s) and return a render-ready result table.

    The task's dependency-aware workflow plan (TaskDescriptor.workflow) is
    executed step by step (execute_workflow): steps run in dependency order
    and produced identifiers are threaded into dependent steps. Tasks without
    a plan fall back to the legacy loop — each intent pipeline runs with the
    same identifiers and the tables merge into one (feature #4). Clause
    attachments scope each pipeline to its own identifier ('email for A and
    phone for B'). Next-step suggestions ride along (feature #10).
    """
    intents = task.get("intents") or [task.get("intent", "resolve")]
    t0 = time.perf_counter()
    try:
        conn = _connect()
    except FileNotFoundError:
        return PipelineResult(
            intent=intents[0],
            intents=intents,
            summary="intelligence.db not found - rebuild it first.",
            error="intelligence.db not found",
        ).to_dict()
    try:
        result = execute_workflow(conn, task)
        if result is None:
            # Legacy path: the task carries no plan (hand-built descriptor)
            # — run the intent list directly, exactly as before.
            scope = _clause_scope(task, task.get("clauses") or [])
            # Only scope when EVERY attached clause intent is actually run —
            # if a clause's intent was dropped from the intents list
            # (static_account subsumes mxcode), its identifiers would vanish
            # from the output. Fall back to the full set so no identifier
            # ever disappears.
            if scope and not set(scope).issubset(intents):
                scope = None
            tables = []
            for intent in intents:
                pipeline = _PIPELINES.get(intent, _pipeline_resolve)
                scoped_task = task
                if scope and intent in scope:
                    scoped_task = dict(task)
                    scoped_task["identifiers"] = scope[intent]
                tables.append(pipeline(conn, scoped_task))
            result = _merge_tables(tables, intents)
            result["workflow_executed"] = []
            result["workflow_chain"] = {}
        result = PipelineResult.from_dict(result)
        result.intents = intents
        result.suggestions = suggest_next_steps(task, result.to_dict())
        result.llm_refined = bool(task.get("llm_refined"))
        result.confidence = task.get("confidence", 0)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "task_executed intents=%s rows=%d elapsed_ms=%.1f",
            ",".join(intents), len(result.rows), elapsed_ms,
            extra={"intents": intents, "rows": len(result.rows),
                   "elapsed_ms": round(elapsed_ms, 1)},
        )
        return result.to_dict()
    finally:
        conn.close()


# ── Clarification engine (feature: ask when the request is ambiguous) ───

# Human labels + one-line descriptions for the clarification options — the
# user sees these when a request could mean several things ("account
# details" -> profile / static account / change history).
CLARIFY_OPTIONS = {
    "profile": ("Full merchant profile",
                "Everything the registry knows: contacts, addresses, banks, sources."),
    "static_account": ("Static account & beneficiary",
                       "Static account number + beneficiary from the Static Account Manager."),
    "change_details": ("Change-of-account history",
                       "Old vs new account, bank and address changes on record."),
    "email": ("Emails", "All email addresses on file."),
    "phone": ("Phones", "All phone numbers on file."),
    "address": ("Addresses", "All street addresses on file."),
    "bank": ("Bank", "The merchant's bank."),
    "account_name": ("Account name", "The name on the merchant's account."),
    "account_number": ("Account number", "The merchant's account number."),
    "payable": ("Payable code", "The merchant's payable code."),
    "alias": ("Aliases", "Every alias on file for the merchant."),
    "contact": ("Contact person", "The merchant's contact person."),
    "onboarded": ("Onboarded date", "When the merchant was onboarded."),
    "state": ("State", "Which state the merchant is in."),
    "source": ("Source file", "Which file/sheet the merchant came from."),
    "beneficiary": ("Beneficiary", "The beneficiary from the Static Account Manager."),
    "mxcode": ("MX codes", "The merchant's MX codes."),
    "tid": ("TIDs", "The merchant's terminal IDs."),
}

# Ambiguity tuning — grounded in the real score spread (see the engine probe):
#   "get account details for medplus"      -> change_details 4.0 / profile 3.0
#   "get the bank details of lagoons"      -> profile 3.0 alone (vague)
#   "get the static account for MX…"       -> static_account 8.0 (conf 96)
#   "get me all the information on medplus"-> profile 10.0 (conf 100)
# A request needs clarification when its phrasing is genuinely ambiguous:
# either two+ intents race within CLARIFY_GAP with no decisive winner, or a
# lone top intent is vague ("details"/"info"/"bank details") under the
# confidence where it would otherwise auto-execute.
CLARIFY_GAP = 4.0      # score gap under which the top two intents "race"
CLARIFY_TOP_MAX = 60   # top confidence must be below this to ask
CLARIFY_VAGUE_INTENTS = ("profile", "change_details")


def top_two_gap(task: Optional[Dict[str, Any]]) -> Optional[float]:
    """Top-2 score gap of a task's intent analysis, or None when fewer than
    two intents scored.

    Mirrors suggest_clarification's race window exactly (same scored-intent
    filter: excluded intents and 'resolve' never count), so the calibration
    log can record precisely the gap a race was asked at — the input the
    gap_threshold fitter learns from.
    """
    if not task:
        return None
    analysis = task.get("analysis") or {}
    excluded = set(task.get("excluded") or [])
    scored = sorted(
        ((i, s["score"]) for i, s in analysis.items()
         if i not in excluded and i != "resolve" and s.get("score", 0) > 0),
        key=lambda kv: kv[1], reverse=True,
    )
    if len(scored) < 2:
        return None
    return round(scored[0][1] - scored[1][1], 3)


def suggest_clarification(
    text: str, task: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Should the app ask the user which interpretation they meant?

    Returns None when the request routes confidently; otherwise
      {"question": str, "options": [{"intent", "label", "description"}]}
    where each option is a pipeline the user can pick to re-run the request
    with that intent forced (detect_task's intent_override).

    Triggers:
      - race: two+ intents score within CLARIFY_GAP and the top intent's
        confidence is below CLARIFY_TOP_MAX (no decisive winner), or
      - vague: a lone vague top intent (profile/change_details via generic
        words like "details"/"info") below the same confidence bound.
    Segment / count / duplicates / summary and resolver fallbacks never
    clarify. Excluded (negated) intents are never offered.

    Remembered choice: when the user answered this exact phrase before and
    checked "remember my choice", the saved interpretation is returned as
    `auto_pick` — the caller (API) executes it directly instead of showing
    the card again.
    """
    t = (text or "").strip()
    if not t:
        return None
    if task is None:
        task = detect_task(t, use_llm=False)
    if not task:
        return None
    # Confidence calibration: when the decision log has enough evidence, the
    # ask/gap thresholds are fitted from real requests instead of the
    # built-in defaults (see calibration.fit). The two are fitted
    # independently — a fitted ask bound never waits on race data, and a
    # fitted race window only applies once enough race outcomes are logged.
    cal = calibration_params()
    top_max = cal["ask_threshold"] if cal["active"] else CLARIFY_TOP_MAX
    gap = cal["gap_threshold"] if cal.get("gap_active") else CLARIFY_GAP
    analysis = task.get("analysis") or {}
    excluded = set(task.get("excluded") or [])
    scored = sorted(
        ((i, s["score"]) for i, s in analysis.items()
         if i not in excluded and i != "resolve" and s.get("score", 0) > 0),
        key=lambda kv: kv[1], reverse=True,
    )
    names = [i for i, _s in scored]
    low = _lower(t)
    # An EXPLICIT field-name match ("get me the tids…", "show the email…")
    # is decisive even when address/name text races other intents with stray
    # words ("…LAGOS STATE" scores state, "…BANK ANTHONY WAY" scores bank).
    # Only fuzzy/semantic hits ('~…') are weak; a raw regex match means the
    # user literally named the field, so never ask. This keeps the address
    # pipeline from clarifying on every pasted address line.
    #
    # Two carve-outs keep the canonical ambiguities working:
    #  - vague intents (profile / change_details): "account details" is
    #    ambiguous even though its phrase regex fires — it still asks;
    #  - vague qualifiers ("bank details", "acct info"): the field word
    #    PLUS "details"/"info" is the account-ish ambiguity the calibration
    #    engine gates ("get the bank details of lagoons"), so those stay
    #    governed by the fitted ask threshold, not short-circuited.
    explicit_top = False
    if scored:
        top_intent = scored[0][0]
        if (top_intent not in CLARIFY_VAGUE_INTENTS
                and not re.search(r"\b(details?|info(?:rmation)?)\b", low)):
            for m in (task.get("analysis") or {}).get(top_intent, {}).get("matched", []):
                if m and not m.startswith("~"):
                    explicit_top = True
                    break
    if explicit_top:
        return None
    if len(scored) >= 2:
        # Multi-intent: clarify when the top two race with no decisive winner
        # ("account details" -> change_details 4.0 vs profile 3.0).
        top, second = scored[0], scored[1]
        top_conf = min(100, int(top[1] * 12))
        if not (top[1] - second[1] <= gap
                and top_conf < top_max):
            return None
        candidates = [i for i, _s in scored[:3]]
    elif len(scored) == 1 and scored[0][0] in CLARIFY_VAGUE_INTENTS \
            and min(100, int(scored[0][1] * 12)) < top_max:
        # Lone vague intent ("get the bank details of lagoons" -> profile via
        # generic 'details') — the account-ish alternatives all fit.
        candidates = [scored[0][0]]
    else:
        return None
    # Always offer every OTHER scored intent too (a raced "account details"
    # usually also means the other account-ish pipelines).
    for i in ("profile", "static_account", "change_details", "email", "phone",
              "address", "mxcode"):
        if i in names and i not in candidates:
            candidates.append(i)
    # Account-ish wording ("account details", "bank details", "acct info")
    # is the canonical ambiguity — always offer the full account trio even
    # when only one scored (so "bank details" can mean the static account).
    if re.search(r"\b(account|acct|bank)\b", low):
        for i in ("profile", "static_account", "change_details"):
            if i not in candidates and i not in excluded:
                candidates.append(i)
    options = [
        {"intent": i, "label": CLARIFY_OPTIONS[i][0],
         "description": CLARIFY_OPTIONS[i][1]}
        for i in candidates if i in CLARIFY_OPTIONS
    ]
    if len(options) < 2:
        return None
    # Remembered choice ("remember my choice"): the phrase key is normalized
    # so 'get account details for medplus' and 'the account details of
    # lagoons' share one key. Only applied when the request WOULD have
    # clarified anyway — a decisive request (e.g. "get the static account
    # for MX…") is never overridden by a saved phrase, and a negated
    # variant ("…but not the change history") keys differently and is never
    # reused. Auto-run the saved intent instead of showing the card.
    remembered = preference_lookup(t, task)
    if remembered and remembered in CLARIFY_OPTIONS \
            and remembered not in excluded:
        saved_options = [
            {"intent": i, "label": CLARIFY_OPTIONS[i][0],
             "description": CLARIFY_OPTIONS[i][1]}
            for i in (remembered, "profile", "static_account",
                      "change_details")
            if i in CLARIFY_OPTIONS and i not in excluded
        ]
        return {
            "question": f"\u201c{t[:140]}\u201d — using your saved choice.",
            "options": saved_options,
            "auto_pick": remembered,
        }
    # Tier 2 — local embedding semantic match (hybrid semantic intent layer,
    # design doc §3/§7/§10). Tier 1 (regex + the ~semantic fallback) is
    # inconclusive here — the request is about to be asked. Consult the
    # embedding tier against per-intent exemplars:
    #   mode "off"     -> nothing (default; zero behavior change)
    #   mode "shadow"  -> log the decision, still ask the user (Phase 1)
    #   mode "enabled" -> a confident Tier-2 winner auto-picks its intent
    #                      instead of the card, same path as a remembered
    #                      choice (Phase 2; gated on the §7 baseline)
    if engine_settings.semantic_tier_mode() != "off":
        from . import semantic
        try:
            t2 = semantic.resolve(t, task)
        except Exception as exc:
            logger.warning("tier2 resolve failed: %s", exc)
            t2 = None
        if t2:
            mode = engine_settings.semantic_tier_mode()
            semantic.log_shadow({
                "ts": time.time(),
                "text": t[:300],
                "mode": mode,
                "tier1_intent": task.get("intent") if task else None,
                "would_clarify": True,
                "tier2_intent": t2["intent"],
                "tier2_exemplar": t2["exemplar"],
                "tier2_confidence": t2["confidence"],
                "tier2_margin": t2["margin"],
                "tier2_would_act": t2["would_act"],
                "encoder": t2["encoder"],
            })
            if (mode == "enabled" and t2["would_act"]
                    and t2["intent"] in CLARIFY_OPTIONS):
                return {
                    "question": (f"\u201c{t[:140]}\u201d — resolved to "
                                 f"{CLARIFY_OPTIONS[t2['intent']][0]}."),
                    "options": options,
                    "auto_pick": t2["intent"],
                    "tier2": {"intent": t2["intent"],
                              "exemplar": t2["exemplar"],
                              "confidence": t2["confidence"],
                              "margin": t2["margin"]},
                }
    return {
        "question": f"\u201c{t[:140]}\u201d could mean a few things — which did you want?",
        "options": options,
    }


def analyze(text: str) -> Dict[str, Any]:
    """Public debug: full intent breakdown for a request (v2).

    Shows every detected intent with its score / confidence / matched
    patterns, the extracted parameters (segment, names, state, has, limit),
    and the resulting task descriptor — the same data the frontend could
    render to explain WHY a request was routed the way it was.
    """
    t = (text or "").strip()
    scores = _analyze(t)
    # The raw scored intents (pre-disambiguation) are the truth for the
    # per-intent breakdown — disambiguation may drop a field intent that the
    # wording DID express ('...with email, top 20' -> email scores but top
    # subsumes it), and the debug panel should show what the engine saw.
    raw_intents = [i for i, _s in sorted(
        scores.items(), key=lambda kv: kv[1]["score"], reverse=True)]
    task = detect_task(t, use_llm=False)
    # The task's ACTUAL intent (after segment/count injection) is the truth;
    # the raw keyword ranking is only for the per-intent breakdown.
    primary = task["intent"] if task is not None else (
        raw_intents[0] if raw_intents else "resolve")
    conf = 0
    if task is not None:
        conf = task.get("confidence", 0)
    elif primary != "resolve":
        conf = scores.get(primary, {}).get("confidence", 0)
    out = {
        "is_task": task is not None,
        "primary": primary,
        "confidence": conf,
        "gap": top_two_gap(task),
        "intents": [
            {"intent": i, "score": scores[i]["score"],
             "confidence": scores[i]["confidence"],
             "matched": scores[i]["matched"]}
            for i in raw_intents if i != "resolve"
        ],
        "identifiers": parse_identifiers(t) if task else {},
        "clauses": task.get("clauses", []) if task else [],
        "segment": task.get("segment", "") if task else "",
        "segment_fields": task.get("segment_fields", []) if task else [],
        "names": task.get("names", []) if task else [],
        "key_merchants": task.get("key_merchants", []) if task else [],
        "params": task.get("params", {}) if task else {},
        "excluded": task.get("excluded", []) if task else [],
        "workflow": task.get("workflow", {}) if task else {},
        "clarification": suggest_clarification(t, task),
        "task": task,
    }
    # Tier 2 explainability (design doc §8): when the semantic tier is on,
    # the debug panel shows what it WOULD decide for this request, so the
    # Rule Engine page can audit shadow decisions without digging into the
    # shadow log. resolve() is lru_cached, so this costs nothing extra when
    # suggest_clarification above already computed it.
    if engine_settings.semantic_tier_mode() != "off":
        from . import semantic
        try:
            out["tier2"] = semantic.resolve(t, task)
        except Exception as exc:
            logger.warning("tier2 resolve failed: %s", exc)
            out["tier2"] = None
    return out
