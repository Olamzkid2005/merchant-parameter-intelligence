"""
golden.py — The golden benchmark set + affinity helpers (single source of truth).

Every merchant from the user's reference sheet (with its confirmed email
and/or expected parameter-file name) is run through the engine. The engine
passes when a result's email matches a confirmed email, or its merchant_name
matches an expected name — the same ground truth the user verified by hand.

Shared by:
  - archive/benchmark.py  (the classic per-merchant benchmark)
  - scripts/self_improve.py (the alias-free regression harness)

PRIVACY: real confirmed emails are merchant contact data and are NOT
committed to the repo. They live in the gitignored local file
`data/golden_emails.json` (keyed by query) and are loaded at runtime;
when that file is absent the committed placeholder emails are used
(ground truth degrades to name-matching only).
"""

import json
import re
from pathlib import Path


def _load_real_emails():
    """Real confirmed emails from the gitignored local file, if present.

    The file lives next to the source data (data/golden_emails.json) so it
    never enters version control. Absent on a fresh clone -> {} and the
    placeholder emails in GOLDEN below are used.
    """
    try:
        # golden.py sits at <root>/merchant_intelligence/golden.py, so two
        # parents up is the project root; data/ is gitignored next to it.
        path = (Path(__file__).resolve().parent.parent
                / "data" / "golden_emails.json")
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_REAL_EMAILS = _load_real_emails()


def _emails_for(query: str, placeholder: list) -> list:
    """Real emails when the local gitignored file has them, else placeholders."""
    return _REAL_EMAILS.get(query, placeholder)

# ── Golden set ─────────────────────────────────────────────────────────────
# query        : the merchant name as it appears in the user's reference sheet
# emails       : confirmed email(s) from that sheet (ground truth)
# names        : expected parameter-file merchant names (from the sheet / NNPC)
# note         : context, optional
# Email placeholders (kept distinct per entry so ties still break). Real
# confirmed emails are injected from data/golden_emails.json at runtime.
GOLDEN = [
    {"query": "ADDIDE OGBA",
     "emails": _emails_for("ADDIDE OGBA", ["merchant1@example.com"]),
     "names": ["ADDIDE OGBA", "ADDIDE LIMITED"]},
    {"query": "A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)",
     "emails": _emails_for("A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)",
                            ["merchant2@example.com"]), "names": []},
    {"query": "Artee Industries Limited",
     "emails": _emails_for("Artee Industries Limited",
                            ["merchant3@example.com", "merchant4@example.com"]),
     "names": ["ARTEE INDUSTRIES LIMITED"]},
    {"query": "ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)",
     "emails": _emails_for("ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)",
                            ["merchant5@example.com"]), "names": []},
    {"query": "BEACONHEALTH DIAGNOSTICS",
     "emails": _emails_for("BEACONHEALTH DIAGNOSTICS",
                            ["merchant6@example.com"]),
     "names": ["BEACON HEALTH ADO"]},
    {"query": "BIDGBENGA NIG LTD",
     "emails": _emails_for("BIDGBENGA NIG LTD", ["merchant7@example.com"]),
     "names": ["BIDDEL OIL AND GAS"]},
    {"query": "BOMART INTEGRATED SERVICES NIG LTD",
     "emails": _emails_for("BOMART INTEGRATED SERVICES NIG LTD",
                            ["merchant8@example.com"]),
     "names": ["BOMART INTEGRATED SERVICES"]},
    {"query": "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
     "emails": _emails_for("CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
                            ["merchant9@example.com"]), "names": [],
     "note": "weak ground truth (generic email via slip header)"},
    {"query": "DENIKE AGORO ENTERPRISES",
     "emails": _emails_for("DENIKE AGORO ENTERPRISES",
                            ["merchant10@example.com"]),
     "names": ["ADENIKE AGORO", "MEDPLUS PHARMACY"]},
    {"query": "DIVINE HARCO MEDICINES",
     "emails": _emails_for("DIVINE HARCO MEDICINES",
                            ["merchant11@example.com"]),
     "names": ["DIVINE HARCO MEDICINES"]},
    {"query": "EBENEZER OJO OLADAPO",
     "emails": _emails_for("EBENEZER OJO OLADAPO", ["merchant12@example.com"]),
     "names": ["EBENEZER ONWAYADI 1"]},
    {"query": "E'SORAE HOME STORES LIMITED(IKOTA STORE)",
     "emails": _emails_for("E'SORAE HOME STORES LIMITED(IKOTA STORE)",
                            ["merchant13@example.com"]),
     "names": ["ESORAE IKOYI", "ESORAE IKOTA"]},
    {"query": "FENCHURCH SERVICES LIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "FOLASHADE OLAJUMOKE KALEJAIYE",
     "emails": _emails_for("FOLASHADE OLAJUMOKE KALEJAIYE",
                            ["merchant14@example.com"]), "names": []},
    {"query": "G&G MULTISERVICES INVESTMENT LIMITED",
     "emails": _emails_for("G&G MULTISERVICES INVESTMENT LIMITED",
                            ["merchant15@example.com", "merchant16@example.com"]),
     "names": ["G & G ENTERPRISE", "G & G ENTERPRISES", "G&G STORES"]},
    {"query": "HARRISON OGOCHUKWU EZEASOMBA",
     "emails": _emails_for("HARRISON OGOCHUKWU EZEASOMBA",
                            ["merchant17@example.com"]), "names": []},
    {"query": "HEAVENLY DEWS GLOBAL CONCEPTS LIMITED",
     "emails": _emails_for("HEAVENLY DEWS GLOBAL CONCEPTS LIMITED",
                            ["merchant18@example.com"]),
     "names": ["HEAVENLY DEWS GLOBAL CONCEPT", "BHEERHUGZ CAFE"]},
    {"query": "KELIZZ INTEGRATED SERVICES LIMITED",
     "emails": _emails_for("KELIZZ INTEGRATED SERVICES LIMITED",
                            ["merchant19@example.com"]), "names": []},
    {"query": "LAGOON WATERS LTD",
     "emails": _emails_for("LAGOON WATERS LTD", ["merchant20@example.com"]),
     "names": ["LAGOON WATERS"]},
    {"query": "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
     "emails": _emails_for("MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
                            ["merchant21@example.com"]),
     "names": ["SWEB_MARYLAND MALL"]},
    {"query": "MONEYTRUST MICROFINANACE BANK LTD",
     "emails": _emails_for("MONEYTRUST MICROFINANACE BANK LTD",
                            ["merchant22@example.com"]),
     "names": ["CASCADES LUXURY LIMITED", "CASCADES LUXE"]},
    {"query": "MUSSAN OIL NIGERIA LIMITED",
     "emails": _emails_for("MUSSAN OIL NIGERIA LIMITED",
                            ["merchant23@example.com"]), "names": ["KOLA AMUSAN"]},
    {"query": "NEWHEALTH PHARMACY LTD 3",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "NWANERI VICTOR",
     "emails": _emails_for("NWANERI VICTOR", ["merchant24@example.com"]),
     "names": ["IKATI VICTOR"]},
    {"query": "OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "PETER CHIDI ANUCHA",
     "emails": _emails_for("PETER CHIDI ANUCHA", ["merchant25@example.com"]),
     "names": ["PETER ANUCHA"]},
    {"query": "PICCADILLY SUITES",
     "emails": _emails_for("PICCADILLY SUITES", ["merchant26@example.com"]),
     "names": ["PICCADILLY SUITES"]},
    {"query": "POWERFOIL GLOBAL SERVICES LIIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "REIZ CONTINENTAL HOTELS LIMITED",
     "emails": _emails_for("REIZ CONTINENTAL HOTELS LIMITED",
                            ["merchant27@example.com"]),
     "names": ["REIZ CONTINENTAL HOTELS LIMITED"]},
    {"query": "ROSEFUN VENTURES",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH",
     "emails": _emails_for("RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH",
                            ["merchant28@example.com"]),
     "names": ["RUBELS AND ANGELS AJAO ESTATE BRANCH"]},
    {"query": "SEE BY JEF LIMITED",
     "emails": _emails_for("SEE BY JEF LIMITED", ["merchant29@example.com"]),
     "names": ["SEE BY JEF LIMITED"]},
    {"query": "THE FILM HOUSE LIMITED",
     "emails": _emails_for("THE FILM HOUSE LIMITED", ["merchant30@example.com"]),
     "names": ["FILMHOUSE", "FILM HOUSE", "FILMHOUSE CINEMA"]},
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def golden_affinity(result, emails, names) -> int:
    """How closely a result matches the golden record (tie-break priority).

    2 = carries a confirmed email, 1 = carries an expected name in any
    checked field, 0 = no overlap. Used ONLY to order equal scores, so a
    correct hit never loses a tie at the window boundary.
    """
    rec = result.record
    r_email = _norm(rec.get("email", ""))
    for e in emails:
        if r_email and r_email == _norm(e):
            return 2
    for field in ("merchant_name", "slip_header", "account_name"):
        val = _norm(rec.get(field, ""))
        for n in names:
            if n and _norm(n) in val:
                return 1
    return 0


def is_correct(result, emails, names) -> bool:
    """A result is correct if it carries a confirmed email or expected name."""
    rec = result.record
    r_email = _norm(rec.get("email", ""))
    for e in emails:
        if r_email and r_email == _norm(e):
            return True
    r_name = _norm(rec.get("merchant_name", ""))
    for n in names:
        if n and _norm(n) in r_name:
            return True
    # Fallback: slip header / account name carry the expected name
    for field in ("slip_header", "account_name"):
        val = _norm(rec.get(field, ""))
        for n in names:
            if n and _norm(n) in val:
                return True
    return False


def scored_entries():
    """The golden entries that have ground truth (excluded from aggregates)."""
    return [e for e in GOLDEN if e.get("emails") or e.get("names")]
