"""
build_exemplars.py — Build the curated Tier-2 exemplar set (data/exemplars.json).

Phase A/B of the hybrid semantic intent layer (docs/hybrid-semantic-intent-layer.md,
§5/§6/§9): per-intent exemplar phrases the embedding tier matches against.
`sematic.py:load_exemplars()` prefers this file when present; the runtime cold
start (the live vocab keyword lists) is only the fallback.

WHY CURATED (vs the keyword cold start): the vocab keyword lists are NOT
intent-pure — e.g. `static_account` carries "alias" and "payable", which are
their own intents, diluting Tier-2 separation. This file is hand-authored so
every phrase belongs to exactly one intent. It is the "approved Phase B
exemplars" the golden set (§7) extends from — kept separate so measuring
Tier-2 against the golden set stays honest (no query is copied verbatim here).

Outputs (both gitignored data/ — auditable on disk / via the Rule Engine,
not in git history):
    data/exemplars.json            {"intents": {<intent>: ["phrase", ...]}}
    data/exemplar_manifest.json    provenance: generated_at, per-intent counts,
                                   source notes, md5 of the exemplars file

Re-runnable and idempotent. Requires no network and no optional deps.

Usage:
    python scripts/build_exemplars.py
"""
import hashlib
import json
import sys
import time
from pathlib import Path

# Windows cp1252 consoles can't encode the ✅/📄 markers — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config
from merchant_intelligence.tasks.vocab import INTENT_KEYWORDS

# ── Curated exemplars ─────────────────────────────────────────────────────
# Canonical pattern language PLUS varied paraphrase anchors, intent-pure:
# no phrase below belongs to another intent's space (that is the whole point
# vs the keyword cold start). Hand-authored; extend from approved golden
# phrasings over time — but never copy a golden query verbatim.
CURATED: dict = {
    "static_account": [
        "static account", "static account number", "static bank details",
        "beneficiary name", "payable account", "settlement account",
        "account we pay into", "where payment is received",
        "remittance account", "funds landing account", "money landing account",
    ],
    "tid": [
        "terminal id", "terminal ids", "tid", "tids", "device id",
        "device ids", "pos terminal", "terminal number", "machine code",
        "terminal details",
    ],
    "mxcode": [
        "mx code", "mxcode", "mx codes", "merchant code", "merchant identifier",
        "mx reference", "lookup key", "system reference for the merchant",
    ],
    "email": [
        "email", "email address", "emails", "e-mail", "mail address",
        "contact email", "official mail", "inbox",
    ],
    "phone": [
        "phone", "phone number", "phone numbers", "mobile number",
        "telephone", "contact number", "hotline", "mobile line", "call line",
    ],
    "address": [
        "address", "addresses", "location", "locations", "physical address",
        "branch location", "street address", "where the store is",
    ],
    "bank": [
        "bank", "bank name", "banks", "banking partner",
        "financial institution", "banking details", "where the money is kept",
    ],
    "account_name": [
        "account name", "account holder", "name on the account",
        "account title", "beneficiary name on the account",
    ],
    "account_number": [
        "account number", "account digits", "account figures",
        "the number of the account", "account numeric",
    ],
    "payable": [
        "payable code", "payable", "payables", "payment code",
        "remittance code", "transfer reference", "payment tag",
    ],
    "alias": [
        "alias", "aliases", "alias code", "other names", "other trading names",
        "name variants", "alternative names",
    ],
    "contact": [
        "contact person", "contact name", "contact person's name",
        "who to contact", "contact details", "liaison", "person in charge",
    ],
    "onboarded": [
        "onboarded date", "onboarding date", "when onboarded", "start date",
        "date joined", "since when active", "when the merchant was onboarded",
    ],
    "state": [
        "state", "which state", "state of operation", "operating state",
        "location state", "which region",
    ],
    "source": [
        "source file", "which file", "which sheet", "source workbook",
        "origin file", "where the record came from", "originating file",
    ],
    "change_details": [
        "change of account details", "change history", "old account",
        "new account", "account changes", "previous account",
        "before and after", "details changed", "account used to be",
    ],
    "profile": [
        "profile", "full profile", "merchant profile", "all information",
        "everything about", "details", "complete details", "full information",
    ],
    "count": [
        "how many", "count", "number of", "total", "how many merchants",
        "tally of", "grand total",
    ],
    "duplicates": [
        "duplicates", "duplicate merchants", "more than once", "appears twice",
        "repeated entries", "listed twice", "double listings",
    ],
    "summary": [
        "summary", "overview", "breakdown", "summary of the file",
        "quick overview", "gist of the file", "overall picture",
    ],
    "related": [
        "related", "linked to", "connected", "associated", "related records",
        "same family", "shares an identity",
    ],
    "formerly": [
        "formerly", "previously known as", "was called", "old name",
        "renamed from", "used to be called",
    ],
    "compare": [
        "compare", "compare merchants", "versus", "difference between",
        "side by side", "how they stack up",
    ],
    "coverage": [
        "coverage", "missing data", "without phone", "missing email",
        "lacking contact", "incomplete records", "never gave us",
    ],
    "top": [
        "top 10", "top banks", "most common", "ranking", "by state",
        "leading states", "biggest banks",
    ],
    "verify": [
        "verify", "is registered", "in the registry", "in the database",
        "confirm exists", "on the system", "exist in our records",
    ],
}


def _build() -> dict:
    """Curated set for every live intent (keyword fallback only for intents
    we have not curated yet — keeps load_exemplars() on the curated file)."""
    out: dict = {}
    for intent in INTENT_KEYWORDS:
        if intent in CURATED:
            out[intent] = list(CURATED[intent])
        else:
            out[intent] = list(INTENT_KEYWORDS[intent])
    return out


def main() -> int:
    exemplars = _build()
    missing = [i for i in INTENT_KEYWORDS if not exemplars.get(i)]
    if missing:
        print(f"  ✗ intents with no exemplars: {missing}")
        return 1

    ex_path = config.DATA_DIR / "exemplars.json"
    ex_path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"intents": exemplars}, indent=2, ensure_ascii=False)
    ex_path.write_text(blob + "\n", encoding="utf-8")

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generator": "scripts/build_exemplars.py",
        "source": ("hand-curated intent-pure phrases (Phase B); keyword "
                   "fallback only for un-curated intents"),
        "exemplars_md5": hashlib.md5(blob.encode("utf-8")).hexdigest(),
        "intent_count": len(exemplars),
        "phrase_count": sum(len(v) for v in exemplars.values()),
        "per_intent": {i: len(v) for i, v in sorted(exemplars.items())},
    }
    (config.DATA_DIR / "exemplar_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    print(f"  ✅ {manifest['phrase_count']} exemplar phrases across "
          f"{manifest['intent_count']} intents")
    print(f"  📄 exemplars.json      -> {ex_path}")
    print(f"  📄 exemplar_manifest   -> {config.DATA_DIR / 'exemplar_manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
