"""
vocab.py — Shared vocabulary and constants for the task engine.

Single home for the identifier kinds, intent patterns (raw + precompiled),
keyword lists, stop-word sets, Nigerian states, request-parameter vocabulary
and the tiny text helpers (_lower / _whole_word_re) that parser.py, intents.py,
pipelines.py and engine.py all depend on. Imported by every other tasks
submodule, imports only config — so it can never create an import cycle.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import config

logger = logging.getLogger(__name__)

ID_KINDS = ("tid", "mxcode", "phone", "email", "account", "static",
            "payable", "bvn", "mid", "alias")

# ── Weighted intent patterns (v2) ────────────────────────────────────────
# Live patterns load from intents.json (tunable by non-developers; the
# MERCHANT_INTENTS_CONFIG env var points at an alternate file). The dicts
# below are the built-in fallbacks used when the file is missing or broken,
# so a config typo can never take the engine down.
#
# Each pattern has a weight. Confidence = min(100, score * 12): a single
# strong phrase ("static account" = 8 -> 96) is high-confidence while a
# generic word ("info" = 3 -> 36) is low and never tips a plain search into
# a task on its own. Keep INTENT_KEYWORDS (plain lists) for the LLM prompt
# and result validation.
_DEFAULT_INTENT_PATTERNS: Dict[str, List[Tuple[str, int]]] = {
    'account_name': [
        (r"\baccount names?\b", 6),
        (r"\baccount holder\b", 5),
    ],
    'account_number': [
        (r"\baccount numbers?\b", 6),
        (r"\bacc numbers?\b", 5),
    ],
    'address': [
        (r"\baddress(?:es)?\b", 5),
        (r"\blocations?\b", 5),
    ],
    'alias': [
        (r"\balias codes?\b", 6),
        (r"\baliases?\b", 5),
        (r"\bassumed\ name\b", 2),
        (r"\bassumed\ name\ codes\b", 2),
        (r"\bfalse\ name\b", 2),
        (r"\bfalse\ name\ codes\b", 2),
    ],
    'bank': [
        (r"\bbank names?\b", 6),
        (r"\bbanks?\b", 3),
        (r"\bwhich banks?\b", 6),
        (r"\bwhat(?:'s| is)? (?:the )?banks?\b", 6),
        (r"\bdeposit\b", 2),
    ],
    'beneficiary': [
        (r"\bbeneficiar(?:y|ies)\b", 5),
    ],
    'change_details': [
        (r"\bchange of account\b", 8),
        (r"\bchange of merchant\b", 8),
        (r"\bchange details\b", 7),
        (r"\baccount change\b", 7),
        (r"\bmerchant change\b", 7),
        (r"\bchanged account\b", 7),
        (r"\bchange history\b", 7),
        (r"\bchanged from\b", 6),
        (r"\bchanged to\b", 6),
        (r"\baccount details\b", 4),
        (r"\bold account\b", 4),
        (r"\bnew account\b", 4),
        (r"\bold bank\b", 4),
        (r"\bnew bank\b", 4),
        (r"\bold address\b", 4),
        (r"\bnew address\b", 4),
        (r"\balter\b", 2),
        (r"\bformer\ account\b", 2),
        (r"\bformer\ address\b", 2),
        (r"\bformer\ bank\b", 2),
        (r"\bfresh\ account\b", 2),
        (r"\bmodify\b", 2),
        (r"\bolder\ account\b", 2),
        (r"\bprevious\ account\b", 2),
        (r"\bprevious\ address\b", 2),
        (r"\bprevious\ bank\b", 2),
        (r"\bshift\b", 2),
        (r"\bswitch\b", 2),
    ],
    'compare': [
        (r"\bcompare\b", 8),
        (r"\bversus\b", 6),
        (r"\bvs\.?\b", 5),
        (r"\bside.by.side\b", 6),
        (r"\bdifference between\b", 5),
        (r"\bcomparison\b", 2),
    ],
    'contact': [
        (r"\bcontact (?:person|persons|name|names)\b", 5),
        (r"\bcontacts?\b", 4),
    ],
    'count': [
        (r"\bhow many\b", 9),
        (r"\btotal number of\b", 8),
        (r"\bcount\b", 7),
        (r"\bhow often\b", 6),
        (r"\bnumber of\b", 4),
        (r"\btally\b", 2),
        (r"\btotal\b", 2),
    ],
    'coverage': [
        (r"\bcoverage\b", 6),
        (r"\bmissing\b", 6),
        (r"\bwith no\b", 6),
        (r"\bhas no\b", 6),
        (r"\bhave no\b", 6),
        (r"\blacking\b", 6),
        (r"\bincomplete\b", 5),
        (r"\bwithout\b", 6),
        (r"\black\b", 2),
        (r"\bmiss\b", 2),
        (r"\bomit\b", 2),
        (r"\buncomplete\b", 2),
    ],
    'dealer_name': [
        (r"\bdealer\s+name\b", 6),
        (r"\btrading\s+name\b", 5),
    ],
    'duplicates': [
        (r"\bduplicates?\b", 8),
        (r"\bappears? more than once\b", 7),
        (r"\bsame merchant\b", 6),
        (r"\brepeated\b", 5),
        (r"\bdouble\b", 2),
        (r"\brepeat\b", 2),
        (r"\breplicate\b", 2),
    ],
    'email': [
        (r"\be[- ]?mails?\b", 5),
        (r"\bmail\b", 2),
        (r"\bpost\b", 2),
    ],
    'formerly': [
        (r"\bformerly\b", 7),
        (r"\brenamed\b", 6),
        (r"\bname variants?\b", 6),
        (r"\bpreviously known\b", 6),
        (r"\bknown as\b", 5),
    ],
    'merchant_id': [
        (r"\bmerchant\s+id\b", 6),
        (r"\bmerchant\s+ids\b", 6),
        (r"\bmerchant\s+identification\b", 5),
    ],
    'mxcode': [
        (r"\bmx[- ]?codes?\b", 6),
        (r"\bmerchant code\b", 5),
    ],
    'onboarded': [
        (r"\bonboarded?\b", 6),
        (r"\bonboarding date\b", 6),
    ],
    'payable': [
        (r"\bpayable codes?\b", 6),
        (r"\bpayables?\b", 5),
    ],
    'phone': [
        (r"\bphone numbers?\b", 6),
        (r"\bmobile numbers?\b", 6),
        (r"\btelephones?\b", 5),
        (r"\bphones?\b", 5),
    ],
    'profile': [
        (r"\bfull profile\b", 7),
        (r"\beverything (?:about|on|for|regarding)\b", 6),
        (r"\beverything\b", 4),
        (r"\banything (?:about|on|for)\b", 5),
        (r"\ball the information\b", 6),
        (r"\bprofile\b", 6),
        (r"\binformation\b", 4),
        (r"\bdetails?\b", 3),
        (r"\binfo\b", 3),
    ],
    'related': [
        (r"\bwho else\b", 8),
        (r"\buses this\b", 8),
        (r"\brelated\b", 7),
        (r"\blinked to\b", 7),
        (r"\bconnected\b", 6),
        (r"\bassociated\b", 6),
        (r"\bassociate\b", 2),
        (r"\battached\b", 2),
        (r"\bconnect\b", 2),
        (r"\bjoined\b", 2),
        (r"\blink\b", 2),
        (r"\brelate\b", 2),
    ],
    'settlement_account': [
        (r"\bsettlement\s+account\b", 8),
        (r"\bdealer\s+account\b", 7),
        (r"\bsettlement\s+acct\b", 7),
        (r"\bsettlement\s+account\s+number\b", 8),
        (r"\bdealer\s+account\s+number\b", 7),
    ],
    'settlement_bank': [
        (r"\bsettlement\s+bank\b", 8),
        (r"\bdealer\s+bank\b", 7),
        (r"\bsettlement\s+bank\s+name\b", 8),
        (r"\bdealer\s+bank\s+name\b", 7),
    ],
    'source': [
        (r"\bwhich (?:file|sheet)\b", 5),
        (r"\bwhat (?:file|sheet)\b", 5),
        (r"\bsources?\b", 3),
        (r"\borigin\b", 2),
    ],
    'state': [
        (r"\bwhich state\b", 5),
        (r"\bwhat state\b", 5),
        (r"\bstate\b", 3),
        (r"\bcountry\b", 2),
    ],
    'static_account': [
        (r"\\bstatic account\\b", 8),
        (r"\bdealer\s+settlement\b", 5),
    ],
    'summary': [
        (r"\bsummar[a-z]*\b", 8),
        (r"\boverview\b", 7),
        (r"\bhigh.level\b", 6),
        (r"\bbreakdown\b", 6),
        (r"\bstats?\b", 5),
    ],
    'tid': [
        (r"\bterminal ids?\b", 6),
        (r"\btids?\b", 4),
    ],
    'top': [
        (r"\btop \d+\b", 7),
        (r"\bmost common\b", 6),
        (r"\bper state\b", 5),
        (r"\bby state\b", 5),
        (r"\branking\b", 5),
        (r"\bmost popular\b", 5),
        (r"\border\b", 2),
        (r"\brank\b", 2),
    ],
    'verify': [
        (r"\bin the registry\b", 6),
        (r"\bin the database\b", 5),
        (r"\bverify\b", 6),
        (r"\bregistered\b", 5),
        (r"\bvalid\b", 4),
        (r"\baffirm\b", 2),
        (r"\bassert\b", 2),
        (r"\brecord\b", 2),
    ],
}




# ── Config loading (intents.json, tunable without code) ───────────────────
# The live INTENT_PATTERNS / INTENT_KEYWORDS come from the JSON file next to
# this module (or the MERCHANT_INTENTS_CONFIG env var). Whole lines starting
# with // or # are comments (JSON itself has none — this makes the file
# friendlier for non-developers).
_INTENTS_FILE = Path(__file__).resolve().parent / "intents.json"


def _strip_comment_lines(text: str) -> str:
    """Drop whole-line // or # comments before JSON parsing."""
    return "\n".join(
        ln for ln in (text or "").splitlines()
        if not ln.lstrip().startswith(("//", "#"))
    )


def _load_intent_config() -> Dict[str, Any]:
    """Load the intent config JSON. Returns {} on any failure so the engine
    falls back to the built-in defaults instead of crashing."""
    path = Path(os.environ.get("MERCHANT_INTENTS_CONFIG") or _INTENTS_FILE)
    try:
        data = json.loads(_strip_comment_lines(path.read_text(encoding="utf-8")))
        if not isinstance(data, dict) or not isinstance(data.get("intents"), dict):
            raise ValueError("'intents' must be an object")
        return data
    except Exception as exc:  # noqa: BLE001 — any config problem -> defaults
        logger.warning("intents config %s failed to load (%s); using defaults",
                       path, exc)
        return {}


def _config_patterns(data: Dict[str, Any]) -> Dict[str, List[Tuple[str, int]]]:
    """Patterns from the config file: {pattern, weight} dicts -> (pattern,
    weight) tuples, exactly the shape of the built-in defaults.

    Bad entries are skipped with a warning instead of raising, so a single
    typo (invalid regex, non-numeric weight) can never take the engine down
    at import — the file docstring promises exactly this.
    """
    out: Dict[str, List[Tuple[str, int]]] = {}
    for intent, spec in (data.get("intents") or {}).items():
        pats = spec.get("patterns") if isinstance(spec, dict) else None
        if not isinstance(pats, list):
            continue
        pairs: List[Tuple[str, int]] = []
        for idx, p in enumerate(pats):
            if not isinstance(p, dict) or not isinstance(p.get("pattern"), str) \
                    or not p["pattern"]:
                logger.warning("intents config: intent %r pattern #%d is not a "
                               "non-empty string; skipped", intent, idx)
                continue
            pat = p["pattern"]
            try:
                re.compile(pat)  # validity probe — compile errors are fatal later
            except re.error as exc:
                logger.warning("intents config: intent %r pattern %r is not "
                               "valid regex (%s); skipped", intent, pat, exc)
                continue
            try:
                weight = int(p.get("weight", 1))
            except (TypeError, ValueError):
                logger.warning("intents config: intent %r pattern %r has "
                               "non-numeric weight %r; using 1",
                               intent, pat, p.get("weight"))
                weight = 1
            pairs.append((pat, weight))
        if pairs:
            out[intent] = pairs
    return out


def _config_keywords(data: Dict[str, Any]) -> Dict[str, List[str]]:
    """Keywords from the config file (plain-word lists for the LLM prompt)."""
    out: Dict[str, List[str]] = {}
    for intent, spec in (data.get("intents") or {}).items():
        kw = spec.get("keywords") if isinstance(spec, dict) else None
        if isinstance(kw, list):
            out[intent] = [str(k) for k in kw]
    return out


def _config_fuzzy(data: Dict[str, Any]) -> Dict[str, bool]:
    """Per-intent fuzzy toggle from the config file (default True when absent
    so existing configs keep typo tolerance)."""
    out: Dict[str, bool] = {}
    for intent, spec in (data.get("intents") or {}).items():
        val = spec.get("fuzzy") if isinstance(spec, dict) else None
        out[intent] = val if isinstance(val, bool) else True
    return out


def _config_slang(data: Dict[str, Any]) -> Dict[str, str]:
    """Request-slang map from the config file's top-level "slang" object
    (defaults when absent). Keys must be lowercase words >= 3 chars — a
    short key would risk hijacking identifiers ('MX', 'PH') or real names.
    Bad entries are skipped with a warning, never fatal."""
    raw = data.get("slang")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        key = str(k).strip().lower()
        val = str(v).strip().lower()
        if len(key) < 3 or not key.isalpha() or not val:
            logger.warning("intents config: slang entry %r -> %r skipped "
                           "(need lowercase word key >= 3 chars)", k, v)
            continue
        out[key] = val
    return out


# Live values: config file wins, built-in defaults fill any gaps. Declared
# empty here and populated by reload_intents() (below) so that function can
# swap them in place — reload keeps `from .vocab import ...` references in
# intents.py / engine.py pointing at the SAME dict objects (clear+update), so
# a UI save hot-reloads the engine without a process restart.
#
# The `if not in globals()` guard is what makes importlib.reload() safe: a
# reload re-executes this module, and a bare `INTENT_PATTERNS = {}` would
# create NEW dict objects while intents.py still holds the OLD ones — silent
# staleness. Guarding keeps the same objects across reloads, so in-place
# updates from reload_intents() always reach the engine.
if "INTENT_PATTERNS" not in globals():
    _INTENT_CONFIG: Dict[str, Any] = {}
    INTENT_PATTERNS: Dict[str, List[Tuple[str, int]]] = {}
    COMPILED_INTENT_PATTERNS: Dict[str, List[Tuple[re.Pattern, int]]] = {}
    INTENT_KEYWORDS: Dict[str, List[str]] = {}
    INTENT_FUZZY: Dict[str, bool] = {}
    INTENT_SLANG: Dict[str, str] = {}

# Per-intent typo-tolerance toggle (default ON). When True, the offline
# semantic tier in intents.py may classify a request whose keywords only
# match within one character edit (~fuzzy) or as a strong paraphrase
# (~semantic) even when no regex pattern fires. Setting an intent's
# "fuzzy": false in intents.json (or the Rule Engine toggle) restricts it
# to exact regex patterns only — useful when typo tolerance produces
# unwanted false positives for a particular intent. Absent = enabled, so
# existing configs keep today's behaviour.
_DEFAULT_INTENT_FUZZY: Dict[str, bool] = {i: True for i in _DEFAULT_INTENT_PATTERNS}

# ── Request-slang expansion (abbreviation normalisation) ─────────────────
# Ops requests are full of short-hands that no pattern or keyword lists
# ('acct mgr', 'deets', 'stmnt', 'numb', 'benef'). intents.py normalises
# the request with this map (word-boundary) BEFORE regex + semantic tiers,
# so slang behaves exactly like its canonical form. Keys are lowercase,
# expansions are lowercase. Tunable by non-developers via the top-level
# "slang" object in intents.json (the Rule Engine editor focuses on
# patterns/keywords; slang is documented in _help).
#
# Safety rule: only map unambiguous domain abbreviations — never a word
# that could be a real merchant token (e.g. "add" -> address is unsafe:
# 'ADDIDE' starts with it, and ADD is a real term). Short keys (< 3 chars)
# are rejected at load so "ph" or "mx" can never hijack identifiers.
_DEFAULT_SLANG: Dict[str, str] = {
    "acct": "account",
    "accts": "accounts",
    "mgr": "manager",
    "deets": "details",
    "stmnt": "statement",
    "stmt": "statement",
    "numb": "number",
    "benef": "beneficiary",
    "addr": "address",
    "mob": "mobile",
    "tel": "telephone",
}

# Plain keyword lists (kept for the LLM prompt and result validation).
_DEFAULT_INTENT_KEYWORDS: Dict[str, List[str]] = {
    "static_account": ["static account", "static acct", "beneficiary", "alias",
                       "payable", "static bank", "acct manager", "account manager"],
    "mxcode": ["mxcode", "mx code", "merchant code"],
    "tid": ["tid", "tids", "terminal id", "terminal ids"],
    "email": ["email", "emails", "e-mail"],
    "phone": ["phone", "telephone", "mobile number"],
    "address": ["address", "addresses", "location", "locations"],
    "bank": ["bank", "banks", "bank name", "which bank", "what bank"],
    "account_name": ["account name", "account holder"],
    "account_number": ["account number", "acc number"],
    "payable": ["payable", "payable code"],
    "alias": ["alias", "alias code"],
    "contact": ["contact", "contact person", "contact name"],
    "onboarded": ["onboarded", "onboarding date"],
    "state": ["state"],
    "source": ["source", "which file", "which sheet"],
    "beneficiary": ["beneficiary"],
    "change_details": ["change of account", "change of merchant", "change details",
                       "account change", "old account", "new account", "old bank",
                       "new bank", "old address", "new address", "change history",
                       "account details"],
    "profile": ["profile", "full profile", "information", "everything about",
                "everything on", "anything about", "anything on", "details",
                "info"],
    "count": ["how many", "count", "number of", "total number of"],
    "duplicates": ["duplicate", "same merchant", "repeated", "more than once"],
    "summary": ["summary", "summarize", "overview", "breakdown", "stats"],
    "related": ["related", "who else", "linked to", "connected", "associated"],
    "formerly": ["formerly", "renamed", "name variants", "known as",
                  "previously known"],
    "compare": ["compare", "versus", "vs", "side by side", "difference between"],
    "coverage": ["coverage", "missing", "without", "with no", "has no",
                  "lacking", "incomplete"],
    "top": ["top", "most common", "per state", "by state", "ranking"],
    "verify": ["verify", "in the registry", "registered", "valid"],
    "settlement_account": ["settlement account", "dealer account", "settlement acct", "settlement account number"],
    "settlement_bank": ["settlement bank", "dealer bank", "settlement bank name", "dealer bank name"],
    "merchant_id": ["merchant id", "merchant ids", "merchant identification"],
    "dealer_name": ["dealer name", "trading name"],
}


def reload_intents() -> Dict[str, Any]:
    """Re-read the intent config (file or MERCHANT_INTENTS_CONFIG override),
    rebuild the live pattern/keyword dicts and swap them IN PLACE so every
    `from .vocab import COMPILED_INTENT_PATTERNS` reference (intents.py,
    engine.py) sees the new data without a process restart. Returns the raw
    config dict (same shape as _load_intent_config)."""
    global _INTENT_CONFIG
    data = _load_intent_config()
    _INTENT_CONFIG = data
    patterns = {**_DEFAULT_INTENT_PATTERNS, **_config_patterns(data)}
    keywords = {**_DEFAULT_INTENT_KEYWORDS, **_config_keywords(data)}
    fuzzy = {**_DEFAULT_INTENT_FUZZY, **_config_fuzzy(data)}
    slang = {**_DEFAULT_SLANG, **_config_slang(data)}
    INTENT_PATTERNS.clear()
    INTENT_PATTERNS.update(patterns)
    INTENT_KEYWORDS.clear()
    INTENT_KEYWORDS.update(keywords)
    INTENT_FUZZY.clear()
    INTENT_FUZZY.update(fuzzy)
    INTENT_SLANG.clear()
    INTENT_SLANG.update(slang)
    COMPILED_INTENT_PATTERNS.clear()
    COMPILED_INTENT_PATTERNS.update({
        intent: [(re.compile(p), weight) for p, weight in pats]
        for intent, pats in patterns.items()
    })
    return data


# Build the live values at import time (single code path for load + reload).
_INTENT_CONFIG = reload_intents()


def intents_source() -> str:
    """Path of the config file actually in use (env override wins)."""
    return str(Path(os.environ.get("MERCHANT_INTENTS_CONFIG") or _INTENTS_FILE))


def get_intent_config() -> Dict[str, Any]:
    """Public read of the raw loaded config — patterns as {pattern, weight}
    dicts, keywords as plain lists, plus _help. Treat the result as read-only."""
    return _INTENT_CONFIG


def default_intent_specs() -> Dict[str, Dict[str, Any]]:
    """Built-in fallback patterns + keywords + fuzzy per intent (UI 'restore
    defaults')."""
    return {
        intent: {
            "patterns": [{"pattern": p, "weight": w} for p, w in pats],
            "keywords": list(_DEFAULT_INTENT_KEYWORDS.get(intent, [])),
            "fuzzy": _DEFAULT_INTENT_FUZZY.get(intent, True),
        }
        for intent, pats in _DEFAULT_INTENT_PATTERNS.items()
    }


def validate_intent_spec(spec: Dict[str, Any]) -> List[str]:
    """Strict validation for UI saves — returns a list of problems ([] = OK).

    Unlike _config_patterns (which silently skips bad entries), this REJECTS
    bad input so the caller (Rule Engine UI) can show the exact error.
    """
    errors: List[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    pats = spec.get("patterns")
    if not isinstance(pats, list) or not pats:
        return ["patterns must be a non-empty list"]
    for idx, p in enumerate(pats):
        if not isinstance(p, dict) or not isinstance(p.get("pattern"), str) \
                or not p["pattern"].strip():
            errors.append(f"pattern #{idx + 1}: must be a non-empty regex string")
            continue
        try:
            re.compile(p["pattern"])
        except re.error as exc:
            errors.append(f"pattern #{idx + 1}: invalid regex — {exc}")
        weight = p.get("weight", 1)
        if not isinstance(weight, int) or isinstance(weight, bool) \
                or not (1 <= weight <= 10):
            errors.append(
                f"pattern #{idx + 1}: weight must be an integer 1-10 (got {weight!r})")
    keywords = spec.get("keywords")
    if not isinstance(keywords, list) \
            or not all(isinstance(k, str) for k in keywords):
        errors.append("keywords must be a list of strings")
    fuzzy = spec.get("fuzzy")
    if fuzzy is not None and not isinstance(fuzzy, bool):
        errors.append("fuzzy must be a boolean (true/false)")
    return errors


def save_intent_config(intent: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one intent's patterns/keywords to the config file, preserving
    any whole-line // or # comments, then hot-reload the live engine dicts.

    `spec` must already pass validate_intent_spec(). Returns the new config.

    Note: the file is re-serialised (json.dumps indent=2) — whole-line
    comments are preserved but hoisted to the top, and the formatting is
    normalised, so hand-tweaked layout is not kept. Semantics never change.
    """
    path = Path(os.environ.get("MERCHANT_INTENTS_CONFIG") or _INTENTS_FILE)
    text = path.read_text(encoding="utf-8")
    comment_lines = [ln for ln in text.splitlines()
                     if ln.lstrip().startswith(("//", "#"))]
    data = json.loads(_strip_comment_lines(text))
    data.setdefault("intents", {})[intent] = spec
    body = json.dumps(data, indent=2, ensure_ascii=False)
    out = ("\n".join(comment_lines) + "\n" if comment_lines else "") + body + "\n"
    path.write_text(out, encoding="utf-8")
    return reload_intents()

# Instruction verbs — presence strongly suggests a request, not a name search.
INSTRUCTION_WORDS = [
    "pls", "please", "get", "find", "lookup", "look up", "show", "give me",
    "use the", "then use", "search for", "extract", "retrieve", "pull",
    "compare", "verify",
]

# Pipelines that can be chained in one request, with a human label for the
# suggestion chips. Key = intent, value = (label, suffix instruction).
CHAINABLE = {
    "tid": ("TIDs", "get the TIDs for these"),
    "mxcode": ("MX codes", "get the MX codes for these"),
    "email": ("Emails", "get the emails for these"),
    "phone": ("Phones", "get the phone numbers for these"),
    "address": ("Addresses", "get the addresses for these"),
    "bank": ("Banks", "get the banks for these"),
    "contact": ("Contact persons", "get the contact persons for these"),
    "onboarded": ("Onboarded dates", "get the onboarded dates for these"),
    "state": ("States", "get the states for these"),
    "source": ("Source files", "get the source files for these"),
    "payable": ("Payable codes", "get the payable codes for these"),
    "alias": ("Aliases", "get the aliases for these"),
    "profile": ("Full profiles", "get the full profiles for these"),
    "static_account": ("Static accounts", "get the static accounts and beneficiaries for these"),
    "verify": ("Verify", "check if these are in the registry"),
    "settlement_account": ("Settlement accounts", "get the settlement accounts for these"),
    "settlement_bank": ("Settlement banks", "get the settlement banks for these"),
    "merchant_id": ("Merchant IDs", "get the merchant IDs for these"),
    "dealer_name": ("Dealer names", "get the dealer names for these"),
}

# Intents whose pipelines can resolve a merchant NAME (not just identifiers).
# Name-only requests ("get me all the information on medplus") only become
# tasks when their intent is in this set — anything else (e.g. an ambiguous
# 'resolve') falls back to a normal name search.
NAME_CAPABLE_INTENTS = {"profile", "email", "phone", "mxcode", "static_account",
                        "change_details", "address", "bank", "account_name",
                        "account_number", "payable", "alias", "contact",
                        "onboarded", "state", "source", "beneficiary",
                        "related", "formerly", "verify", "count", "duplicates",
                        "summary", "tid",
                        "settlement_account", "settlement_bank",
                        "merchant_id", "dealer_name"}

# Tokens stripped from a request when extracting the merchant name
# (extract_names). Subset-union of QUERY_NOISE_WORDS + intent vocabulary —
# anything that is instruction or request-language, not part of the name.
# All upper-cased so the comparison against extracted (upper-case) words is
# exact.
NAME_STOP_WORDS = ({w.upper() for w in config.QUERY_NOISE_WORDS} | {
    "STATIC", "ACCOUNT", "ACCT", "BENEFICIARY", "BANK", "EMAIL",    "EMAILS",
    "E_MAIL",    "MX", "MXCODE", "MXCODES", "CODE", "CODES",
    # File/report-system words are request-language ("from d parameter file",
    # "static account report server") — never part of a merchant name or a
    # segment fragment.
    "PARAMETER", "PARAMETERS", "PARAMS", "SERVER", "SERVERS",
    "REPORT", "REPORTS", "WORKBOOK", "SPREADSHEET", "EXCEL",
    "PLATFORM", "SYSTEM", "SYSTEMS", "PORTAL", "MASTER", "APP",
    "APPLICATION", "APPLICATIONS",
    "TID", "TIDS", "MERCHANT", "MERCHANTS", "TERMINAL", "TERMINALS",
    "NUMBER", "NUMBERS", "DATA", "DATABASE", "RECORDS", "RECORD", "ABOVE",
    "CHANGE", "CHANGED", "CHANGES", "DETAILS", "DETAIL", "OLD", "NEW",
    "BELOW", "THESE", "THOSE", "FROM", "VIA", "USING", "USE", "USED",
    "HELP", "PLEASE", "PLS", "PLZ", "KINDLY",
    "ALIAS", "ALIASES", "PAYABLE", "PAYABLES", "MAPPED", "MAPPING",
    "MAPPED TO", "MAPPINGS",
    "ADDRESS", "ADDRESSES", "LOCATION", "LOCATIONS",
    "BANK", "BANKS", "CONTACT", "CONTACTS", "PERSON", "PERSONS",
    "ONBOARDED", "ONBOARDING", "STATE", "STATES", "SOURCE", "SOURCES",
    "FILE", "FILES", "SHEET", "SHEETS", "SLIP", "HEADER", "SERIAL",
    "REGISTRY", "REGISTERED", "VALID", "EXIST", "EXISTS", "EXISTING",
    "FORMERLY", "RENAMED", "RENAME", "CALLED", "KNOWN", "AS", "WAS",
    "PROFILE", "PROFILES", "INFO", "INFORMATION", "EVERYTHING",
    "ANYTHING",
    "WERE", "WHAT", "WHICH", "DOES", "DO", "DID", "USE", "USED", "USING",
    "PREVIOUSLY", "ORIGINALLY", "VARIANTS", "VARIANT",
    "COMPARE", "VERSUS", "VS", "RELATED", "LINKED", "CONNECTED",
    "ASSOCIATED", "NETWORK", "VERIFY", "VERIFICATION", "CHECK",
    "COVERAGE", "MISSING", "LACKING", "INCOMPLETE", "TOP", "RANKING",
    "RANKINGS", "MOST", "COMMON", "POPULAR", "WHO", "ELSE", "EVERYWHERE",
    "MENTION", "MENTIONS", "MENTIONED",
    "STATIC ACCT", "ACCT MANAGER", "STATIC ACCOUNT MANAGER",
    "THEN", "FIRST", "NEXT", "BOTH", "STEP", "PIPELINE", "MANAGER",
    "HAVE", "HAS", "THAT", "THIS", "THERE", "IS", "ARE", "BE", "NOT",
    "ALSO", "PLUS", "MORE", "TOO", "THANKS", "THANK", "REGARDS", "DEAR",
    "HELLO", "HI", "GOOD", "MORNING", "AFTERNOON", "EVENING",
    # Template/instruction words that appear inside pasted request templates
    # ("Please retrieve this merchant's MXCODE…") and must never leak into an
    # extracted merchant name.
    "MERCHANT'S", "MERCHANTS'", "OBTAIN", "OBTAINS", "RETRIEVE", "RETRIEVES",
    "RETRIEVAL", "NAME", "NAMES", "PLACEHOLDER", "REQUEST", "REQUESTED",
})

# Key merchant families the ops team works with daily (MEDPLUS, ADDIDE,
# SPAR/ARTEE, FILMHOUSE, LAGOON WATERS, CASCADES …). A bare
# "<key-merchant> <field>" request ("medplus emails", "addide addresses",
# "spar phone number", "lagoon waters address") is treated as a task even
# without an instruction verb or question word, because these roots are
# unambiguous — while a generic name + field word ("PALM GROVE ADDRESS")
# stays a normal search. Roots are matched as whole words or name prefixes
# ("ADDIDE APATA" starts with "ADDIDE").
#
# Multi-word roots are exact/prefix-only for the fuzzy typo branch, so the
# CASCADES LUXE/LUXURY variants are listed BEFORE the bare CASCADES root — a
# request naming the specific family reports the specific root, and every
# CASCADES-* row still routes through the family prefix. LAGOON WATERS sits
# near NNPC because the DB holds LAGOON WATERS LTD - NNPC rows.
#
# DB-grounded additions (probed against intelligence.db): BOKKU MART and
# ORIENT AFRICA are the user-named chains (BOKKU MART- ILAJE AJAH, ORIENT
# AFRICA COMPANY LTD-NNPC MEGA STATION), and SHOPRITE / KONGAPAY / GENESIS
# FOODS are the big unclaimed merchant families by row count (160 / 189 /
# 412 rows). MONEYTRUST has no merchant_name rows of its own — it resolves
# through the alias engine to CASCADES LUXURY — so it stays as a root for
# the alias probe. Platform/bank aggregates (ZINTERNET, TRACTION, MFBs) are
# deliberately NOT added: they are payment platforms, not key merchant
# families, and would flood the key-merchant task gate with noise.
KEY_MERCHANT_ROOTS = (
    "MEDPLUS", "ADDIDE", "SPAR", "ARTEE", "FILMHOUSE",
    "CASCADES LUXE", "CASCADES LUXURY", "CASCADES",
    "MONEYTRUST", "RUBELS", "BEACONHEALTH", "JUST CHIPS", "LAGOON WATERS",
    "BOKKU MART", "ORIENT AFRICA", "SHOPRITE", "KONGAPAY", "GENESIS FOODS",
    "NNPC",
)

# Field-intent vocabulary that must never survive name extraction in a
# NAME-ONLY field request ("get medplus phone and email" -> MEDPLUS, not
# MEDPLUS PHONE). Applied by engine.py AFTER extract_names when the top
# intent is a field-extraction intent and the request is single-line — the
# pasted name-list path ("RELIABLE PHONES AND GADGET" is a real merchant)
# never strips these.
FIELD_NAME_STOPS = {
    # Bare MAIL/MAILS deliberately absent: the email pattern requires the
    # e- prefix (\be[- ]?mails?\b), so 'MAIL' in a real merchant name
    # ("AUTO MAIL") never triggers email — and must never be stripped.
    "email": {"EMAIL", "EMAILS", "E-MAIL", "E_MAIL"},
    "phone": {"PHONE", "PHONES", "TELEPHONE", "TELEPHONES", "MOBILE",
               "MOBILES", "NUMBER", "NUMBERS"},
    # Profile: "medplus full profile" / "spar everything" must search
    # MEDPLUS / SPAR, never the trailing request words. FULL is safe here
    # because stripping is trailing-only — "get the profile of FULL HOUSE"
    # (a real-name middle word) is never touched.
    "profile": {"PROFILE", "PROFILES", "FULL", "INFO", "INFORMATION",
                "EVERYTHING", "ANYTHING", "DETAILS", "DETAIL"},
    "address": {"ADDRESS", "ADDRESSES", "LOCATION", "LOCATIONS"},
    # bank is deliberately ABSENT: merchants legitimately contain the word
    # ("get me the bank for ACCESS BANK" must keep the full name).
    "tid": {"TID", "TIDS", "TERMINAL", "TERMINALS", "ID", "IDS"},
    "mxcode": {"MX", "MXCODE", "MXCODES", "CODE", "CODES"},
    "account_name": {"ACCOUNT", "ACCOUNTS", "NAME", "NAMES", "HOLDER"},
    "account_number": {"ACCOUNT", "ACCOUNTS", "NUMBER", "NUMBERS", "ACC"},
    "payable": {"PAYABLE", "PAYABLES", "CODE", "CODES"},
    "alias": {"ALIAS", "ALIASES", "CODE", "CODES"},
    "contact": {"CONTACT", "CONTACTS", "PERSON", "PERSONS", "NAME", "NAMES"},
    "onboarded": {"ONBOARDED", "ONBOARDING", "DATE", "DATES"},
    "state": {"STATE", "STATES"},
    "source": {"SOURCE", "SOURCES", "FILE", "FILES", "SHEET", "SHEETS"},
    "beneficiary": {"BENEFICIARY", "BENEFICIARIES"},
}

# ── Segment intent (collection requests: "all the addresses of all NNPC") ──
# Field words map a requested column to its label. Detection requires BOTH a
# collective marker ("all/every/each/list of") AND a field word so a plain
# merchant name like "ALL STAR STORES" never misroutes into a segment task.
SEGMENT_FIELDS = {
    "address": ["address", "addresses", "location", "locations"],
    "email": ["email", "emails", "e-mail"],
    "phone": ["phone", "phones", "telephone", "telephones",
               "mobile number", "mobile numbers"],
    "mxcode": ["mxcode", "mx code", "mx codes"],
    "tid": ["tid", "tids", "terminal id", "terminal ids"],
    "merchant": ["merchant", "merchants", "merchant name", "merchant names",
                 "station", "stations", "outlet", "outlets", "store",
                 "stores", "branch", "branches", "family", "families",
                 "chain", "chains", "group", "groups", "franchise"],
    "contact": ["contact", "contacts", "contact person", "contact persons",
                 "contact name", "contact names"],
    "account": ["account", "accounts", "account number", "account numbers"],
    "bank": ["bank", "banks", "bank name", "bank names"],
    "state": ["state", "states"],
    "onboarded": ["onboarded", "onboarding", "onboarding date"],
}

# Collective markers. "all" alone is weak (see ALL STAR STORES) — detection
# also requires an instruction verb OR a strong marker (all the / list of /
# every / each).
SEGMENT_COLLECTIVE = ("all", "every", "each", "list of", "list all",
                      "list every", "all of", "all the")

# Words stripped when extracting the segment fragment from the request.
SEGMENT_STOP_WORDS = ({
    "all", "every", "each", "list", "any", "of", "the", "and", "with",
    "which", "no", "without", "missing", "lacking", "per", "by", "do",
    "does", "has", "for", "in", "from", "on", "that", "these", "those",
    "their", "show", "get", "give", "find", "pull", "extract", "retrieve",
    "print", "display", "please", "pls", "kindly", "me", "us", "my",
    "our",
    "station", "stations", "outlet", "outlets", "store", "stores",
    "branch", "branches", "terminal", "terminals", "merchant", "merchants",
    "service", "services",    "sheet", "file", "workbook", "database", "db",
    "record", "records", "entry", "entries", "info", "information",
    "details", "data", "have", "has", "currently", "mapped", "mappings",
    "above", "below", "following", "respectively",
    # Source-system words: "from d parameter file", "on the static account
    # report server" — the fragment is the MERCHANT, never the file it lives
    # in, so these must never survive stop-word stripping.
    "parameter", "parameters", "param", "params", "server", "servers",
    "report", "reports", "platform", "system", "systems", "portal",
    "master", "app", "application", "applications", "spreadsheet",
    "excel", "batch", "template",
}) | {w.lower() for w in NAME_STOP_WORDS}

# Order of identifier kinds for parsing + reporting.

# Whole-word matching ("(?<![a-z])word(?![a-z])") is used in several hot
# paths — _match_fields, _looks_like_segment, _state_in_text, _anchored_name.
# Compiled once per keyword and cached so detection never re-compiles the
# same boundary pattern on every request.
_WHOLE_WORD_CACHE: Dict[str, re.Pattern] = {}


def _whole_word_re(word: str) -> re.Pattern:
    """Cached compiled regex for a whole-word match (case-sensitive on the
    caller's casing, boundary-safe so 'all' never matches inside 'ball')."""
    pat = _WHOLE_WORD_CACHE.get(word)
    if pat is None:
        pat = re.compile(r"(?<![a-z])%s(?![a-z])" % re.escape(word))
        _WHOLE_WORD_CACHE[word] = pat
    return pat


def _lower(text: str) -> str:
    return (text or "").lower()


def _normalize(text: str) -> str:
    """Lowercase + expand request slang to its canonical form (word-boundary).

    'get the acct mgr deets for 2ISW916B' ->
    'get the account manager details for 2isw916b'. Applied to the request
    BEFORE the regex and semantic tiers, so abbreviation variants behave
    exactly like the canonical words the patterns/keywords are written in.
    Only lowercase word keys >= 3 chars (guarded at load) are replaced — an
    identifier like 'MX141692' or a real name like 'ADDIDE' can never be
    touched. Keys are applied longest-first so 'accts' is expanded before
    'acct' could half-match it. Never raises; returns the original on any
    surprise.

    NOTE: patterns/keywords are matched against this NORMALISED text, so
    they must be written in canonical form ('account', not 'acct') — slang
    forms live in the "slang" map, not in patterns.
    """
    low = _lower(text)
    if not low or not INTENT_SLANG:
        return low
    try:
        for k in sorted(INTENT_SLANG, key=lambda x: -len(x)):
            v = INTENT_SLANG[k]
            low = re.sub(r"(?<![a-z])%s(?![a-z])" % re.escape(k), v, low)
    except Exception:
        return low
    return low


SAFE_SHORT_STATES = {"LA", "FCT"}
NIGERIA_STATES = {
    "ABIA": ["ABIA", "AB"], "ADAMAWA": ["ADAMAWA", "AD"],
    "AKWA IBOM": ["AKWA IBOM", "AKWAIBOM", "AK"], "ANAMBRA": ["ANAMBRA", "AN"],
    "BAUCHI": ["BAUCHI", "BA"], "BAYELSA": ["BAYELSA", "BY"],
    "BENUE": ["BENUE", "BE"], "BORNO": ["BORNO", "BO"],
    "CROSS RIVER": ["CROSS RIVER", "CROSSRIVER", "CR"], "DELTA": ["DELTA", "DT"],
    "EBONYI": ["EBONYI", "EB"], "EDO": ["EDO", "ED"],
    "EKITI": ["EKITI", "EK"], "ENUGU": ["ENUGU", "EN"],
    "FCT": ["FCT", "ABUJA", "FEDERAL CAPITAL"], "GOMBE": ["GOMBE", "GO"],
    "IMO": ["IMO", "IM"], "JIGAWA": ["JIGAWA", "JI"],
    "KADUNA": ["KADUNA", "KD"], "KANO": ["KANO", "KN"],
    "KATSINA": ["KATSINA", "KT"], "KEBBI": ["KEBBI", "KB"],
    "KOGI": ["KOGI", "KG"], "KWARA": ["KWARA", "KW"],
    "LAGOS": ["LAGOS", "LA"], "NASARAWA": ["NASARAWA", "NA"],
    "NIGER": ["NIGER", "NG"], "OGUN": ["OGUN", "OG"],
    "ONDO": ["ONDO", "OD"], "OSUN": ["OSUN", "OS"],
    "OYO": ["OYO", "OY"], "PLATEAU": ["PLATEAU", "PL"],
    "RIVERS": ["RIVERS", "RV"], "SOKOTO": ["SOKOTO", "SO"],
    "TARABA": ["TARABA", "TA"], "YOBE": ["YOBE", "YO"],
    "ZAMFARA": ["ZAMFARA", "ZM"],
}

# Address vocabulary — a pasted line is treated as an ADDRESS (matched
# against the address column, never fuzzy name-searched) when it carries
# road-type vocabulary AND a locality word (state/city/area). Address-type
# words are the strong gate: 'MEDPLUS PHARMACY', 'RELIABLE PHONES AND
# GADGET', 'BOKKU MART' contain none, so ordinary merchant names never
# misroute.
ADDRESS_TYPE_WORDS = frozenset({
    "ROAD", "STREET", "AVENUE", "AVE", "BOULEVARD", "BLVD", "PLAZA",
    "MALL", "ESTATE", "WAY", "DRIVE", "LANE", "CLOSE", "CRESCENT",
    "BLOCK", "PLOT", "BUILDING", "JUNCTION", "GARDEN", "CENTER",
    "CENTRE", "PHASE", "EXPRESSWAY", "BYPASS", "VILLAGE", "QUARTERS",
    "GATE", "TERMINAL", "AIRPORT", "HOUSE", "SUITE", "FLOOR",
    "CIRCLE", "PARK", "GARDENS", "SQUARE", "VI", "GRA",
    "STATE",  # 'LAGOS STATE' — a state name followed by STATE is address-
               # like even without a road word ('MEDPLUS MARINA LAGOS
               # ISLAND, LAGOS STATE'). The locality gate still applies, so
               # plain merchant names never trip on it.
})

# Localities: states (all NIGERIA_STATES aliases) + the cities / areas that
# appear in real address pastes. These words are dropped from an address
# query's matching tokens so '...LEKKI, LAGOS' doesn't collapse every
# candidate onto Lagos rows.
ADDRESS_LOCALITY_WORDS = frozenset({
    "ABUJA", "IKEJA", "LEKKI", "IKORODU", "SURULERE", "SANGOTEDO",
    "IKOYI", "YABA", "MARYLAND", "FESTAC", "AJAH", "OTA", "ONIRU",
    "ILUPEJU", "MAGODO", "ALIMOSHO", "EGBEDA", "AMUWO", "PALMGROOVE",
    "GBAGADA", "ILORIN", "KANO", "KADUNA", "HARCOURT", "ENUGU",
    "OWERRI", "JOS", "CALABAR", "UYO", "WARRI", "ASABA", "ONITSHA",
    "AWKA", "OSOGBO", "AKURE", "IBADAN", "ABEOKUTA", "BENIN",
    "UMUAHIA", "EFFURUN", "BARNAWA", "MAITAMA", "WUSE", "GWARINPA",
    "DUTSE", "UTAKO", "LUGBE", "IKOTA", "IKATE", "ELEGUSHI",
    "AJAO", "OGUDU", "CHEVRON", "AKOWONJO", "BARIGA", "AGUNGI",
    "FCT", "F.C.T", "VICTORIA", "ISLAND", "TOWN", "BEACH",
} | {a.upper() for aliases in NIGERIA_STATES.values() for a in aliases})

# Sources that win when the same address exists in several files: the newest
# Medplus workbooks are the user's stated priority (Medplus.xlsx store
# directory + its static-account file).
ADDRESS_SOURCE_PRIORITY = (
    "MEDPLUS", "STATIC ACCOUNT TERMINAL  MEDPLUS",
)


# Presence filters: "with email", "has phone", "with address" -> the
# pipeline requires that column to be non-empty.
PRESENCE_PATTERNS = {
    "email": [r"\bwith\s+(?:an\s+)?e[- ]?mail\b",
              r"\bhas\s+(?:an\s+)?e[- ]?mail\b",
              r"\be[- ]?mails?\s+(?:only|present)\b",
              r"\bthat\s+have\s+e[- ]?mail\b"],
    "phone": [r"\bwith\s+(?:a\s+)?phone\b", r"\bhas\s+(?:a\s+)?phone\b",
               r"\bwith\s+phone\s+number\b"],
    "address": [r"\bwith\s+(?:an\s+)?address\b", r"\bhas\s+(?:an\s+)?address\b"],
}

MAX_RESULT_LIMIT = 5000

# Hard cap on a single pasted request. The identifier classifier runs a DB
# membership probe per token, so an unbounded paste (a full Excel dump, a
# pasted webpage) would hammer the registry. Guard here AND at the API layer
# (api.py returns a clean 400 instead of letting the error surface as a 500).
MAX_INPUT_CHARS = 50_000


SEGMENT_EXTRA_STOP = {
    "HOW", "MANY", "TOP", "FIRST", "LAST", "TOTAL", "NUMBER", "NUMBERS",
    "COUNT", "DUPLICATE", "DUPLICATES", "SUMMARIZE", "SUMMARY", "SUMMARISE",
    "SUMMARIZES", "OVERVIEW", "BREAKDOWN", "STATS", "THERE", "ARE", "IS",
    "FIND", "SHOW", "LIST", "REPEATED", "ONCE", "MORE", "ANY",
}

# Negation markers: phrases that EXCLUDE the intent they point at
# ("get account details but not the change history" -> change_details is out).
# Bare "not"/"without" are deliberately absent — "show me the stations
# without email" is a presence filter (merchants lacking email), not an
# intent exclusion.
NEGATION_MARKERS = (
    "but not", "except for", "except", "excluding", "exclude",
    "other than", "not the", "not a", "not an", "don't want",
    "do not want", "no need for", "skip", "leave out", "omit",
)

# Intent dependency graph: what each pipeline needs (resolved internally) and
# what it produces. Drives build_execution_plan() — a plan never duplicates a
# sub-step (static_account requires mxcode, which its pipeline resolves
# internally), and the UI renders the ordered workflow.
INTENT_GRAPH = {
    "static_account": {"requires": ["mxcode"],
                       "produces": ["static_acc_no", "beneficiary",
                                    "payable", "alias"]},
    "tid": {"requires": [], "produces": ["tid"]},
    "mxcode": {"requires": [], "produces": ["mxcode"]},
    "email": {"requires": [], "produces": ["email"]},
    "phone": {"requires": [], "produces": ["phone"]},
    "address": {"requires": [], "produces": ["address"]},
    "bank": {"requires": [], "produces": ["bank"]},
    "account_name": {"requires": [], "produces": ["account_name"]},
    "account_number": {"requires": [], "produces": ["account_number"]},
    "payable": {"requires": [], "produces": ["payable_code"]},
    "alias": {"requires": [], "produces": ["alias"]},
    "contact": {"requires": [], "produces": ["contact_name"]},
    "onboarded": {"requires": [], "produces": ["onboarded_date"]},
    "state": {"requires": [], "produces": ["state"]},
    "source": {"requires": [], "produces": ["sheet_name"]},
    "beneficiary": {"requires": [], "produces": ["beneficiary"]},
    "related": {"requires": [], "produces": ["linked records"]},
    "formerly": {"requires": [], "produces": ["name history"]},
    "compare": {"requires": [], "produces": ["comparison"]},
    "coverage": {"requires": [], "produces": ["missing coverage"]},
    "top": {"requires": [], "produces": ["rankings"]},
    "verify": {"requires": [], "produces": ["found/not-found"]},
    "profile": {"requires": [], "produces": ["profile", "contacts",
                                              "addresses"]},
    "change_details": {"requires": [],
                        "produces": ["old_account", "new_account"]},
    "segment": {"requires": [], "produces": ["requested fields"]},
    "count": {"requires": [], "produces": ["count"]},
    "duplicates": {"requires": [], "produces": ["clusters"]},
    "summary": {"requires": [], "produces": ["stats"]},
    "settlement_account": {"requires": [], "produces": ["dealer_account_no"]},
    "settlement_bank": {"requires": [], "produces": ["dealer_bank_name"]},
    "merchant_id": {"requires": [], "produces": ["merchant_id"]},
    "dealer_name": {"requires": [], "produces": ["dealer_name"]},
}

# Human step verbs for the workflow view ("resolve_mxcode ->
# fetch_static_account" reads like a plan).
WORKFLOW_STEPS = {
    "static_account": "fetch_static_account",
    "tid": "fetch_tid",
    "mxcode": "resolve_mxcode",
    "email": "fetch_email",
    "phone": "fetch_phone",
    "address": "fetch_address",
    "bank": "fetch_bank",
    "account_name": "fetch_account_name",
    "account_number": "fetch_account_number",
    "payable": "fetch_payable",
    "alias": "fetch_alias",
    "contact": "fetch_contact",
    "onboarded": "fetch_onboarded",
    "state": "fetch_state",
    "source": "fetch_source",
    "beneficiary": "fetch_beneficiary",
    "related": "find_related",
    "formerly": "find_formerly",
    "compare": "compare_merchants",
    "coverage": "coverage_check",
    "top": "rank_top",
    "verify": "verify",
    "profile": "build_profile",
    "change_details": "fetch_change_details",
    "segment": "collect_segment",
    "count": "count_records",
    "duplicates": "find_duplicates",
    "summary": "summarize",
    "settlement_account": "fetch_settlement_account",
    "settlement_bank": "fetch_settlement_bank",
    "merchant_id": "fetch_merchant_id",
    "dealer_name": "fetch_dealer_name",
}

# Preposition anchors: the merchant name is the phrase AFTER the last anchor
# ("get me all the information on LAGOON WATERS" -> "LAGOON WATERS").
NAME_ANCHORS = ("regarding", "about", "on", "of", "for", "named", "called")

# Light stop set for ANCHORED name extraction: only generic/noise words, so
# intent vocabulary that is really part of a name ("FIRST BANK", "IBADAN
# STORE") is preserved. The heavy NAME_STOP_WORDS stays for the word-splat
# fallback.
LIGHT_NAME_STOPS = ({w.upper() for w in config.QUERY_NOISE_WORDS}
                    | {w.upper() for w in config.GENERIC_WORDS})
