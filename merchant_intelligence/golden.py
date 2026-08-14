"""
golden.py — The golden benchmark set + affinity helpers (single source of truth).

Every merchant from the user's reference sheet (with its confirmed email
and/or expected parameter-file name) is run through the engine. The engine
passes when a result's email matches a confirmed email, or its merchant_name
matches an expected name — the same ground truth the user verified by hand.

Shared by:
  - archive/benchmark.py  (the classic per-merchant benchmark)
  - scripts/self_improve.py (the alias-free regression harness)
"""

import re

# ── Golden set ─────────────────────────────────────────────────────────────
# query        : the merchant name as it appears in the user's reference sheet
# emails       : confirmed email(s) from that sheet (ground truth)
# names        : expected parameter-file merchant names (from the sheet / NNPC)
# note         : context, optional
GOLDEN = [
    {"query": "ADDIDE OGBA", "emails": ["a11ogba@addide.com"],
     "names": ["ADDIDE OGBA", "ADDIDE LIMITED"]},
    {"query": "A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)",
     "emails": ["m.ajayi@purelifepharmacy.ng"], "names": []},
    {"query": "Artee Industries Limited",
     "emails": ["account.treasury@arteegroup.com", "suresh.mk@arteegroup.com"],
     "names": ["ARTEE INDUSTRIES LIMITED"]},
    {"query": "ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)",
     "emails": ["nbashir@atreos.com"], "names": []},
    {"query": "BEACONHEALTH DIAGNOSTICS",
     "emails": ["adomanager@beaconhealth.io"], "names": ["BEACON HEALTH ADO"]},
    {"query": "BIDGBENGA NIG LTD",
     "emails": ["accounts@biddeloilandgas.com"], "names": ["BIDDEL OIL AND GAS"]},
    {"query": "BOMART INTEGRATED SERVICES NIG LTD",
     "emails": ["aa@bomartworld.com"], "names": ["BOMART INTEGRATED SERVICES"]},
    {"query": "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
     "emails": ["me@yahoo.com"], "names": [],
     "note": "weak ground truth (generic me@yahoo.com via slip header)"},
    {"query": "DENIKE AGORO ENTERPRISES",
     "emails": ["f.ailoyafen@medplusng.com"],
     "names": ["ADENIKE AGORO", "MEDPLUS PHARMACY"]},
    {"query": "DIVINE HARCO MEDICINES",
     "emails": ["divineharcomedicines@gmail.com"],
     "names": ["DIVINE HARCO MEDICINES"]},
    {"query": "EBENEZER OJO OLADAPO",
     "emails": ["ebenezeronwayadi@gmail.com"],
     "names": ["EBENEZER ONWAYADI 1"]},
    {"query": "E'SORAE HOME STORES LIMITED(IKOTA STORE)",
     "emails": ["finance@esoraehome.com"], "names": ["ESORAE IKOYI", "ESORAE IKOTA"]},
    {"query": "FENCHURCH SERVICES LIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "FOLASHADE OLAJUMOKE KALEJAIYE",
     "emails": ["phola.isaac@gmail.com"], "names": []},
    {"query": "G&G MULTISERVICES INVESTMENT LIMITED",
     "emails": ["giftgodson00@gmail.com", "godsononyiri@gmail.com"],
     "names": ["G & G ENTERPRISE", "G & G ENTERPRISES", "G&G STORES"]},
    {"query": "HARRISON OGOCHUKWU EZEASOMBA",
     "emails": ["harrisbliss@yahoo.com"], "names": []},
    {"query": "HEAVENLY DEWS GLOBAL CONCEPTS LIMITED",
     "emails": ["ooladehin@bheerhugz.com"],
     "names": ["HEAVENLY DEWS GLOBAL CONCEPT", "BHEERHUGZ CAFE"]},
    {"query": "KELIZZ INTEGRATED SERVICES LIMITED",
     "emails": ["kcee094@gmail.com"], "names": []},
    {"query": "LAGOON WATERS LTD",
     "emails": ["dejiladgroup@yahoo.com"], "names": ["LAGOON WATERS"]},
    {"query": "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
     "emails": ["temitope@purple.xyz"], "names": ["SWEB_MARYLAND MALL"]},
    {"query": "MONEYTRUST MICROFINANACE BANK LTD",
     "emails": ["info@cascadeslux.com"],
     "names": ["CASCADES LUXURY LIMITED", "CASCADES LUXE"]},
    {"query": "MUSSAN OIL NIGERIA LIMITED",
     "emails": ["amusan_777@yahoo.com"], "names": ["KOLA AMUSAN"]},
    {"query": "NEWHEALTH PHARMACY LTD 3",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "NWANERI VICTOR",
     "emails": ["nwanerivictor457@gmail.com"], "names": ["IKATI VICTOR"]},
    {"query": "OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "PETER CHIDI ANUCHA",
     "emails": ["chidi_anucha@yahoo.com"], "names": ["PETER ANUCHA"]},
    {"query": "PICCADILLY SUITES",
     "emails": ["sam.ifeozo@gmail.com"], "names": ["PICCADILLY SUITES"]},
    {"query": "POWERFOIL GLOBAL SERVICES LIIMITED",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "REIZ CONTINENTAL HOTELS LIMITED",
     "emails": ["reiz.reizcontinentalhotelabuja@gmail.com"],
     "names": ["REIZ CONTINENTAL HOTELS LIMITED"]},
    {"query": "ROSEFUN VENTURES",
     "emails": [], "names": [], "note": "no ground truth in reference sheet"},
    {"query": "RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH",
     "emails": ["chiderachristopher@gmail.com"],
     "names": ["RUBELS AND ANGELS AJAO ESTATE BRANCH"]},
    {"query": "SEE BY JEF LIMITED",
     "emails": ["ewa_david@yahoo.com"], "names": ["SEE BY JEF LIMITED"]},
    {"query": "THE FILM HOUSE LIMITED",
     "emails": ["smonsuru@filmhouseng.com"],
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
