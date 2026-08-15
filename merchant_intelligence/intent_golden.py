"""
intent_golden.py — The held-out novel-phrasings set for the intent parser.

Phase 0 of the hybrid semantic intent layer (docs/hybrid-semantic-intent-layer.md,
§7): a set of REALISTIC requests for each intent, phrased in ways the current
regex patterns never anticipated. Each query carries the intent a user would
expect it to resolve to. This is the ONLY intent-level ground truth in the
codebase (golden.py is a merchant-*matching* benchmark and carries zero intent
labels), so it doubles as the ongoing intent golden set: extended over time
from approved Phase B exemplars, kept separate from golden.py's merchant set.

NOVELTY CONTRACT — enforced by scripts/phase0_baseline.py at run time:
    For each entry, the EXPECTED intent must NOT be reachable by a raw regex
    match (only the offline ~semantic/~fuzzy fallback or nothing). A query
    that literally contains one of its intent's pattern phrases measures
    nothing — Tier 1 already handles it, so it must be replaced. The harness
    fails loudly on any violation.

PRIVACY: this file contains only request phrasing — no merchant contact data,
no real identifiers — so it is safe to commit (unlike golden.py's emails).

Conventions:
    query   the request exactly as a user might type it (mixed case ok)
    intent  the expected intent, one of INTENT_KEYWORDS (or "segment")
    note    why this phrasing is novel / what it probes (optional)
"""

# Queries use key merchants (MEDPLUS, SPAR, ADDIDE, LAGOON WATERS, ...) and
# file segments ("the NNPC file") so the requests read exactly like ops
# language — the same phrasings the Rule Engine's pattern mining would learn
# from real usage if users actually typed them.
INTENT_GOLDEN = [
    # ── static_account ────────────────────────────────────────────────────
    {"query": "who handles the money for MEDPLUS",
     "intent": "static_account",
     "note": "no 'static account' / 'beneficiary' / 'payable' wording at all"},
    {"query": "where does the payout for LAGOON WATERS land",
     "intent": "static_account",
     "note": "payout phrasing; none of the static_account regexes fire"},
    {"query": "which account do we settle ADDIDE into",
     "intent": "static_account",
     "note": "'settle' is close to settlement, but no pattern word appears"},
    {"query": "the recipient details for MONEYTRUST",
     "intent": "static_account",
     "note": "'recipient' instead of 'beneficiary'"},

    # ── tid ───────────────────────────────────────────────────────────────
    {"query": "the device ids for LAGOON WATERS",
     "intent": "tid",
     "note": "'device ids' never appears in tid patterns"},
    {"query": "what terminals does SPAR operate",
     "intent": "tid",
     "note": "bare 'terminals' — patterns require 'terminal id' or 'tids'"},
    {"query": "list the pos machines for MEDPLUS",
     "intent": "tid",
     "note": "'pos machines' is terminal language, no regex word"},

    # ── mxcode ────────────────────────────────────────────────────────────
    {"query": "what is the merchant identifier for MEDPLUS",
     "intent": "mxcode",
     "note": "'identifier' vs pattern 'merchant code'"},
    {"query": "the mx reference for LAGOON WATERS",
     "intent": "mxcode",
     "note": "'mx reference' — pattern needs 'mx code'/'mxcode'"},
    {"query": "which code identifies SHOPRITE in the system",
     "intent": "mxcode",
     "note": "verb 'identifies' carries the meaning"},

    # ── email ─────────────────────────────────────────────────────────────
    {"query": "the official mail of BEACONHEALTH",
     "intent": "email",
     "note": "bare 'mail' — pattern requires e-mail/email/emails"},
    {"query": "get the mail for SHOPRITE",
     "intent": "email",
     "note": "'mail' without the leading e misses every email regex"},

    # ── phone ─────────────────────────────────────────────────────────────
    {"query": "how do I reach LAGOON WATERS",
     "intent": "phone",
     "note": "'reach' is contact language, no phone word"},
    {"query": "the call line for MEDPLUS",
     "intent": "phone",
     "note": "'call line' instead of 'phone/mobile/telephone'"},
    {"query": "what number should I dial for SPAR",
     "intent": "phone",
     "note": "'dial' carries the intent"},

    # ── address ───────────────────────────────────────────────────────────
    {"query": "where is SPAR situated",
     "intent": "address",
     "note": "'situated' — patterns use address/location"},
    {"query": "the physical spot for ADDIDE",
     "intent": "address",
     "note": "'physical spot' instead of 'address/location'"},
    {"query": "which street is FILMHOUSE on",
     "intent": "address",
     "note": "'street' is a location word, absent from patterns"},

    # ── bank ──────────────────────────────────────────────────────────────
    {"query": "which financial institution serves MEDPLUS",
     "intent": "bank",
     "note": "'financial institution' — no 'bank' token"},
    {"query": "the banking partner of ADDIDE",
     "intent": "bank",
     "note": "'banking' is not the word-boundary pattern 'banks?'"},
    {"query": "where does SPAR keep its money",
     "intent": "bank",
     "note": "colloquial 'keeps its money'"},

    # ── account_name ──────────────────────────────────────────────────────
    {"query": "what name is on the account for MEDPLUS",
     "intent": "account_name",
     "note": "word order defeats 'account name'/'account holder' patterns"},
    {"query": "the holder of the account for MONEYTRUST",
     "intent": "account_name",
     "note": "'holder of the account' — pattern is 'account holder'"},

    # ── account_number ────────────────────────────────────────────────────
    {"query": "the digits of the account for MEDPLUS",
     "intent": "account_number",
     "note": "'digits' — no 'account number' phrase"},
    {"query": "the account figures for LAGOON WATERS",
     "intent": "account_number",
     "note": "'figures' is numeric language, no pattern word"},

    # ── payable ───────────────────────────────────────────────────────────
    {"query": "what's the payment code for ADDIDE",
     "intent": "payable",
     "note": "'payment code' — pattern is 'payable code'"},
    {"query": "the remittance code for SPAR",
     "intent": "payable",
     "note": "'remittance' is settlement language, no pattern word"},

    # ── alias ─────────────────────────────────────────────────────────────
    {"query": "what other names does ARTEE go by",
     "intent": "alias",
     "note": "'other names' / 'go by' — no alias/formerly pattern word"},
    {"query": "the other trading names for MEDPLUS",
     "intent": "alias",
     "note": "'trading names' instead of 'alias'"},

    # ── contact ───────────────────────────────────────────────────────────
    {"query": "who is the person in charge at MEDPLUS",
     "intent": "contact",
     "note": "'person in charge' — no 'contact' token"},
    {"query": "who do we call at LAGOON WATERS",
     "intent": "contact",
     "note": "'who do we call' is contact language"},

    # ── onboarded ─────────────────────────────────────────────────────────
    {"query": "when did MEDPLUS join the network",
     "intent": "onboarded",
     "note": "'join the network' instead of 'onboarded'"},
    {"query": "how long has SPAR been with us",
     "intent": "onboarded",
     "note": "tenure phrasing, no pattern word"},

    # ── state ─────────────────────────────────────────────────────────────
    {"query": "which region is LAGOON WATERS in",
     "intent": "state",
     "note": "'region' — patterns use 'state'"},
    {"query": "which city zone is ADDIDE in",
     "intent": "state",
     "note": "'city zone' — location/state language, no pattern word"},

    # ── source ────────────────────────────────────────────────────────────
    {"query": "which workbook did MEDPLUS come from",
     "intent": "source",
     "note": "'workbook' — patterns use file/sheet"},
    {"query": "the origin file for SPAR",
     "intent": "source",
     "note": "'origin file' — no 'which file'/'which sheet' wording"},

    # ── change_details ────────────────────────────────────────────────────
    {"query": "the account history for LAGOON WATERS",
     "intent": "change_details",
     "note": "'account history' — pattern is 'change history'"},
    {"query": "what did SPAR's account used to be",
     "intent": "change_details",
     "note": "'used to be' carries the before/after meaning"},
    {"query": "have the details for ADDIDE ever changed",
     "intent": "change_details",
     "note": "'details ... changed' split across the sentence"},

    # ── profile ───────────────────────────────────────────────────────────
    {"query": "tell me all there is to know about MEDPLUS",
     "intent": "profile",
     "note": "'all there is to know about' — 'everything about' / bare 'everything' both miss"},
    {"query": "the full lowdown on SPAR",
     "intent": "profile",
     "note": "'lowdown' instead of profile/information/details"},

    # ── count ─────────────────────────────────────────────────────────────
    {"query": "the total tally of merchants in the NNPC file",
     "intent": "count",
     "note": "'total tally' — no 'how many'/'count'/'number of'"},
    {"query": "the sum of merchants in the MRSP file",
     "intent": "count",
     "note": "'sum' is arithmetic language, no pattern word"},

    # ── duplicates ────────────────────────────────────────────────────────
    {"query": "which merchants show up twice in the registry",
     "intent": "duplicates",
     "note": "'show up twice' — no 'duplicate'/'more than once'"},
    {"query": "which names are listed twice in the NNPC file",
     "intent": "duplicates",
     "note": "'listed twice' instead of 'repeated'"},

    # ── summary ───────────────────────────────────────────────────────────
    {"query": "give me the gist of the NNPC file",
     "intent": "summary",
     "note": "'gist' — no summary/overview/breakdown word"},
    {"query": "the overall picture of the MRSP file",
     "intent": "summary",
     "note": "'overall picture' instead of 'overview'"},

    # ── related ───────────────────────────────────────────────────────────
    {"query": "what else is tied to LAGOON WATERS",
     "intent": "related",
     "note": "'tied to' — pattern is 'linked to'"},
    {"query": "who shares an identity with MEDPLUS",
     "intent": "related",
     "note": "'shares an identity' — no related/connected/associated word"},

    # ── formerly ──────────────────────────────────────────────────────────
    {"query": "what did MEDPLUS use to be called",
     "intent": "formerly",
     "note": "'used to be called' — no 'formerly'/'known as'"},
    {"query": "the old name of SPAR",
     "intent": "formerly",
     "note": "'old name' — not 'name variants'/'formerly'"},

    # ── compare ───────────────────────────────────────────────────────────
    {"query": "how do SPAR and ADDIDE stack up",
     "intent": "compare",
     "note": "'stack up' — no compare/versus/vs wording"},
    {"query": "which is bigger between SPAR and ADDIDE",
     "intent": "compare",
     "note": "'which is bigger between' — no pattern word"},

    # ── coverage ──────────────────────────────────────────────────────────
    {"query": "which MEDPLUS outlets are short a phone number",
     "intent": "coverage",
     "note": "'short a phone number' — no missing/without/has-no wording"},
    {"query": "which SPAR branches never gave us an email",
     "intent": "coverage",
     "note": "'never gave us' instead of 'missing'"},

    # ── top ───────────────────────────────────────────────────────────────
    {"query": "the biggest banks in the NNPC file",
     "intent": "top",
     "note": "'biggest' — pattern is 'top N'/'most common'/'ranking'"},
    {"query": "the leading states in the MRSP file",
     "intent": "top",
     "note": "'leading' instead of 'top'/'ranking'"},

    # ── verify ────────────────────────────────────────────────────────────
    {"query": "is MEDPLUS on the system",
     "intent": "verify",
     "note": "'on the system' — no 'in the registry'/'registered'"},
    {"query": "does SPAR exist in our records",
     "intent": "verify",
     "note": "'exist in our records' instead of 'verify/registered'"},

    # ── segment (collection requests) ─────────────────────────────────────
    {"query": "all the addresses of all nnpc stations",
     "intent": "segment",
     "note": "collective-marker phrasing; segment is injected, not regex-scored"},
    {"query": "every terminal across all NNPC locations",
     "intent": "segment",
     "note": "'every ... across all' collective phrasing"},
]


def for_intent(intent: str):
    """Every golden entry expected to resolve to `intent`."""
    return [e for e in INTENT_GOLDEN if e.get("intent") == intent]


def intents_covered() -> list:
    """Distinct expected intents in the set, in first-appearance order."""
    seen = []
    for e in INTENT_GOLDEN:
        if e["intent"] not in seen:
            seen.append(e["intent"])
    return seen
