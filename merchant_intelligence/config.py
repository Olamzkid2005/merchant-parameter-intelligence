"""
Configuration — edit paths and settings to match your environment.

MANUAL_ALIASES and the KNOWN_* compound lists can be edited either here OR
in the external JSON data files (data/manual_aliases.json and
data/known_compounds.json). When the JSON files exist they override the
built-in defaults below — so you can teach the engine without touching code.
"""
import json
import os
from pathlib import Path

# ── File paths ─────────────────────────────────────────────────────────────
# Project root = parent of the merchant_intelligence package (robust to moves)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# The Excel workbook to search
EXCEL_FILE = DATA_DIR / "2ISW_Parameter_File 5.xlsx"

# Where the SQLite database lives
DB_DIR = DATA_DIR
DB_FILE = DB_DIR / "merchant_search.db"

# The universal intelligence database — every Excel sheet in a folder is
# ingested into this single file by build_intelligence_db.py.
INTELLIGENCE_DB = DB_DIR / "intelligence.db"


def active_db() -> Path:
    """Return the database the app should load for everything.

    Precedence:
      1. $MERCHANT_DB env var — explicit override (points at any .db)
      2. intelligence.db       — built from ALL Excel files in the folder
      3. merchant_search.db    — legacy fallback (pre-intelligence setups)

    This lets the app transparently switch to the intelligence database the
    moment it has been built, without any code changes.
    """
    override = os.environ.get("MERCHANT_DB")
    if override:
        return Path(override)
    if INTELLIGENCE_DB.exists():
        return INTELLIGENCE_DB
    return DB_FILE

# JSON cache where auto-learned alias mappings are persisted (Phase 10)
ALIAS_CACHE_FILE = DATA_DIR / "merchant_aliases.json"

# ── Column keyword mapping ─────────────────────────────────────────────────
# Phase 1: Auto-discover columns by matching these keywords (case-insensitive)
COLUMN_KEYWORDS = {
    "merchant_name": [
        "merchant", "business name", "trading name", "outlet",
        "merchant name", "company name", "organisation", "organization",
        "legal name", "store name", "settlement name",
        "dba", "doing business as", "customer name",
    ],
    "slip_header": [
        "slip header", "slipheader", "dba name", "receipt name",
    ],
    "merchant_id": [
        "merchant id", "merchantid", "mid", "merchant code",
        "merchant number", "merchant no",
    ],
    "mxcode": [
        "mxcode", "mx code", "mx",
    ],
    "payable_code": [
        "payable", "payable code", "payable id", "payableid",
    ],
    "tid": [
        "tid", "terminal id", "terminalid", "terminal number",
        "terminal no", "terminal",
    ],
    "terminal_serial": [
        "serial", "terminal serial", "serial number",
    ],
    "email": [
        "email", "e-mail", "mail",
    ],
    "phone": [
        "phone", "mobile", "telephone", "tel", "phone number",
        "mobile phone", "mobile number", "contact phone",
    ],
    "address": [
        "address", "location", "street", "city", "town",
        "lga", "local government",
    ],
    "contact_name": [
        "contact name", "contact person", "contact",
    ],
    "contact_title": [
        "title", "contact title",
    ],
    "account_name": [
        "account name", "account", "settlement account",
    ],
    "account_number": [
        "account no", "account number", "acct no", "acct num",
        "bank account", "settlement account number",
    ],
    "bank": [
        "bank", "bank name", "financial institution",
    ],
    "state": [
        "state", "state code",
    ],
    "bvn": [
        "bvn", "bank verification number",
    ],
    "ptsp": [
        "ptsp", "ptsp code",
    ],
    "terminal_type": [
        "terminal type", "terminal model", "device type", "device model",
    ],
    "deployment_status": [
        "deployment status", "status", "sim status",
    ],
    "remarks": [
        "remark", "remarks", "reason", "comment", "description", "narrative",
    ],
    "alias": [
        "alias", "ussd alias", "ussd", "short code",
    ],
    "static_acc_no": [
        "static acc", "static account", "staticacc",
    ],
    "date": [
        "date", "created", "created date", "date generated",
    ],
}

# ── Generic / stop words to strip when generating aliases (Phase 2) ──────
GENERIC_WORDS = [
    "LIMITED", "LTD", "NIGERIA", "NG", "GLOBAL", "SERVICES",
    "ENTERPRISES", "ENT", "INVESTMENT", "INVESTMENTS", "INV",
    "COMPANY", "CO", "CORPORATION", "CORP", "INC", "INCORPORATED",
    "GROUP", "HOLDINGS", "HOLDING", "PLC", "INTERNATIONAL", "INTL",
    "TECHNOLOGIES", "TECH", "SOLUTIONS", "SYSTEMS", "CONSULTING",
    "CONSULTANCY", "VENTURES", "PROPERTIES", "REALTY", "ESTATE",
    "PRODUCTS", "INDUSTRIES", "IND", "FZC", "LLC", "FZE", "DMCC",
    "NIG",
    # Stop words — filter out to prevent spurious matches
    "THE", "A", "AN", "AND", "OR", "FOR", "TO", "IN", "ON", "AT",
    "BY", "WITH", "OF", "IS", "IT", "AS", "BE", "THIS", "THAT",
]

# ── Alias generation rules (Phase 2) ───────────────────────────────────────
# Manual alias pairs — add your own here (or edit data/manual_aliases.json)
# Format: "QUERY MERCHANT NAME": ["DB RECORD NAME", "alias2", ...]
_DEFAULT_MANUAL_ALIASES = {
    # ── Already confirmed from reference data ────────────────────────────
    "THE FILM HOUSE LIMITED": [
        "Filmhouse", "Film House", "Filmhouse Cinema",
        "Filmhouse IMAX", "IMAX", "Filmhouse IMAX Lekki",
        "FILMHOUSE CINEMA - CIRCLE MALL",
        "FILMHOUSE CINEMA - IKOTA (BLACKBELL)",
    ],
    "ARTEE INDUSTRIES LIMITED": [
        # The workbook contains 340 rows named exactly "ARTEE INDUSTRIES
        # LIMITED" (some carry account.treasury@arteegroup.com). Artee Group
        # operates the SPAR franchise in Nigeria, so SPAR store searches
        # resolve here. The value is the real DB record name.
        "ARTEE INDUSTRIES LIMITED",
    ],
    # SPAR store names -> Artee (SPAR Nigeria franchise operator). Multi-token
    # keys ONLY — a bare "SPAR" key would substring-hijack unrelated names
    # (SPARE PARTS, MONNASPARK, SPAR SUPERMARKET rows) via the fuzzy lookup
    # branch in AliasEngine.lookup(). Bare "SPAR" still resolves through the
    # fuzzy branch against these keys ("SPAR" in "SPAR NIGERIA").
    # NOTE: lookup() returns the FIRST matching key in dict order — if a
    # future SPAR-* key mapping elsewhere is added BEFORE these three in the
    # JSON, bare-SPAR resolution silently changes. Keep SPAR keys together.
    "SPAR NIGERIA": [
        "ARTEE INDUSTRIES LIMITED",
    ],
    "SPAR LEKKI": [
        "ARTEE INDUSTRIES LIMITED",
    ],
    "SPAR IKEJA": [
        "ARTEE INDUSTRIES LIMITED",
    ],
    "BEACONHEALTH DIAGNOSTICS": [
        "Beacon Health", "BeaconHealth", "Beacon",
        "BEACON HEALTH ADO",
    ],
    "SQUAREONE CONCEPTS LIMITED": [
        "Square One", "Square One Concepts",
    ],
    "DENIKE AGORO ENTERPRISES": [
        "ADENIKE AGORO", "Denike Agoro", "Adenike Agoro",
        "MEDPLUS PHARMACY",
    ],
    "EBENEZER OJO OLADAPO": [
        "EBENEZER ONWAYADI 1", "Ebenezer Onwayadi", "Ebenezer Ojo",
    ],
    "NWANERI VICTOR": [
        "IKATI VICTOR", "Nwaneri Victor",
        "EAGLE FLIGHT MICROFINANCE BANK",
    ],
    "BIDGBENGA NIG LTD": [
        "BIDDEL OIL AND GAS", "Biddel Oil", "Biddeloilandgas",
    ],

    # ── New mappings from reference data confirmation ────────────────────
    "ADDIDE OGBA": [
        "ADDIDE OGBA", "ADDIDE LIMITED",
    ],
    "A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)": [
        "A- PURE LIFESTYLE PHARMACY NIGERIA LIMITED",
        "A-PURE LIFESTYLE PHARMACY",
    ],
    "ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)": [
        "ATREOS RETAIL PLATFORM LIMITED",
    ],
    "BOMART INTEGRATED SERVICES NIG LTD": [
        "BOMART INTEGRATED SERVICES NIG LTD.",
    ],
    # CRANE FIELD INTERNMATIONAL SCHOOL JEDDO resolves to the EAGLE FLIGHT
    # MICROFINANCE BANK record whose slip_header reads "CRANEFIELD INT'L
    # SCHOOL" (row 37857) — confirmed via the reference sheet note
    # "used slipheader to find it" with email me@yahoo.com (row 41619,
    # same merchant name). EAGLE FLIGHT MFB is the processing entity; the
    # school is identified by its slip header on those rows.
    "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO": [
        "EAGLE FLIGHT MICROFINANCE BANK",
        "CRANEFIELD INT'L SCHOOL",
    ],
    "DIVINE HARCO MEDICINES": [
        "DIVINE HARCO MEDICINES",
    ],
    "E'SORAE HOME STORES LIMITED(IKOTA STORE)": [
        "ESORAE IKOYI", "ESORAE IKOTA",
    ],
    "FOLASHADE OLAJUMOKE KALEJAIYE": [
        "FOLASHADE KALEJAIYE",
    ],
    "HARRISON OGOCHUKWU EZEASOMBA": [
        "HARRISON EZEASOMBA",
    ],
    "HEAVENLY DEWS GLOBAL CONCEPTS LIMITED": [
        "HEAVENLY DEWS GLOBAL CONCEPT", "BHEERHUGZ CAFE",
    ],
    "KELIZZ INTEGRATED SERVICES LIMITED": [
        "KELIZZ INTEGRATED SERVICES",
    ],
    "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT": [
        "SWEB_MARYLAND MALL",
    ],
    "MONEYTRUST MICROFINANACE BANK LTD": [
        "CASCADES LUXURY LIMITED",
    ],
    # Full-name spellings users actually type — resolve to the same record
    "MONEYTRUST MICROFINANCE": [
        "CASCADES LUXURY LIMITED",
    ],
    "MONEYTRUST MICROFINANCE BANK": [
        "CASCADES LUXURY LIMITED",
    ],
    "MONEYTRUST MICROFINANCE BANK LTD": [
        "CASCADES LUXURY LIMITED",
    ],
    "MONEYTRUST MICROFINANCE BANK LIMITED": [
        "CASCADES LUXURY LIMITED",
    ],
    "MUSSAN OIL NIGERIA LIMITED": [
        "KOLA AMUSAN",
        "WHITEVILL HOTEL",
        "WHITE VILL HOSPITALITY AND HOMES",
    ],
    "PICCADILLY SUITES": [
        "PICCADILLY SUITES", "PICCADILLY POOL BAR",
    ],
    "REIZ CONTINENTAL HOTELS LIMITED": [
        "REIZ CONTINENTAL HOTELS LIMITED",
    ],
    "RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH": [
        "RUBELS AND ANGELS AJAO ESTATE BRANCH",
    ],
    "SEE BY JEF LIMITED": [
        "SEE BY JEF LIMITED",
    ],
    "G&G MULTISERVICES INVESTMENT LIMITED": [
        "G & G ENTERPRISE",
        "G & G ENTERPRISES",
        "G&G STORES",
        "G&G ELECTRICAL",
    ],

    # ── NNPC file alias mappings (confirmed from NNPC parameter files) ──
    "PETER CHIDI ANUCHA": [
        "PETER ANUCHA",                    # MX183526 in NNPC Batch 1 — confirmed
    ],
    "LAGOON WATERS LTD": [
        "LAGOON WATERS",                   # MX183544, MX183549 in NNPC Batch 1 — confirmed
    ],

    # ── Display name overrides ───────────────────────────────────────────
    # Map OLD names → new display name for MX183639
    "TEGRA-EAGLES CONCEPT INT'L LTD": [
        "TEGRA EAGLES CONCEPT INTL LTD. - NNPC",
    ],
    "TEGRA EAGLES CONCEPT": [
        "TEGRA EAGLES CONCEPT INTL LTD. - NNPC",
    ],
}


def _load_alias_overrides() -> dict:
    """Load data/manual_aliases.json if present, else the built-in defaults.

    The external file is the source of truth when it exists — it lets you
    teach the engine (add/remove alias mappings) without editing Python.
    """
    try:
        path = DATA_DIR / "manual_aliases.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
    except Exception:
        pass
    return _DEFAULT_MANUAL_ALIASES


MANUAL_ALIASES = _load_alias_overrides()

# ── Learned alias cache (Phase 10 — auto-learning) ─────────────────────────
# Auto-learned aliases get stored here when the engine discovers new mappings.
# Format: "DB_MERCHANT_NAME": {"search": "QUERY_USED", "aliases": ["alias1", ...]}

# ── Scoring weights (Phase 5) ──────────────────────────────────────────────
# Each field mapped to its weight in the overall confidence score (0-100).
# Editable externally via data/field_weights.json ({"weights": {...}}) — the
# calibrate_weights.py harness writes suggested weights there after fitting
# against the golden set.
_DEFAULT_FIELD_WEIGHTS = {
    "merchant_name":    300,   # dominant — exact name match should give ~9/10
    "slip_header":      40,    # second most important field
    "mxcode":           20,
    "payable_code":     10,
    "tid":              20,
    "account_name":     10,
    "email":            15,
    "phone":            10,
    "address":          15,
    "contact_name":      5,
    "terminal_serial":   5,
    "alias":            10,
}


def _load_field_weights() -> dict:
    """Load data/field_weights.json ({"weights": {...}}) if present, else
    the built-in defaults. The external file is the source of truth when it
    exists — calibrated weights override the hand-set ones without touching
    Python."""
    try:
        path = DATA_DIR / "field_weights.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            weights = data.get("weights", {})
            if isinstance(weights, dict) and weights:
                return {k: int(v) for k, v in weights.items()}
    except Exception:
        pass
    return dict(_DEFAULT_FIELD_WEIGHTS)


FIELD_WEIGHTS = _load_field_weights()

# ── Signal floor (scoring dilution fix) ────────────────────────────────────
# compute_overall() ignores fields that score BELOW this value when computing
# the weighted average. Empty/unrelated fields (score 0-25) previously added
# their weight to the denominator, capping a perfect merchant_name match at
# ~65/100 — the reason genuine full-name hits scored 6.5-7.5 and never reached
# "Exact Match" without an identifier/alias boost. Only fields with real
# signal now contribute. A floor (not "any non-zero") also keeps weak fuzzy
# noise out of the denominator.
SIGNAL_FLOOR = 30.0

# ── Code-name boost ────────────────────────────────────────────────────────
# When merchant_name is a numeric code (e.g. "4789.0", "5411.0"), multiply
# the weights of slip_header and account_name by this factor to allow
# secondary-column matches to lift the overall score.
CODE_NAME_BOOST = 8.0           # 8x boost for slip_header + account_name

# ── Alias probe window ─────────────────────────────────────────────────────
# How many DB rows to retrieve per alias target. Generous (120) because
# high-cardinality merchants can have 60-210 rows with the same name (ATREOS
# 63, ARTEE 210) — a tight window truncates the rows carrying the REAL email
# (e.g. ATREOS rows 41749+ with NBASHIR@ATREOS.COM), leaving only bare
# email='Y' siblings boosted.
ALIAS_PROBE_LIMIT = 120

# ── Alias-match field floor ─────────────────────────────────────────────────
# When an alias CONFIRMS a merchant (merchant_name + alias are forced to 100),
# secondary fields that score below this floor are dropped from the weighted
# average. An unrelated populated field would otherwise drag the alias-confirmed
# row below a bare row whose fields are empty — burying the correct record
# outside the result window. 30 was too low: a noise slip_header=32 on the
# KOLA AMUSAN rows diluted them to 92.5 while bare WHITEVILL rows hit 100.0.
# 70 keeps only genuinely strong secondary matches (token overlap / substring).
# 50 was too permissive once the fuzzy ratios improved: a weak-but-real email
# field (e.g. "nbashir@atreos.com" vs the ATREOS query tokens) started scoring
# ~50-60, was kept, and dragged the alias-confirmed row to 97.7 — below the
# bare 100.0 sibling rows, burying the email-carrying record outside the top.
ALIAS_MIN_FIELD_SCORE = 70.0

# ── Matching thresholds ────────────────────────────────────────────────────
EXACT_MATCH_THRESHOLD   = 95   # >= this = Exact Match
HIGH_CONF_THRESHOLD     = 80   # >= this = High Confidence
POSSIBLE_THRESHOLD      = 50   # >= this = Possible Match

# ── Decisive-match family guard ────────────────────────────────────────────
# When a NAME search (not an identifier search) wins with this score or
# better, the profile only expands its family from records of the SAME
# merchant as the winner. Low-scoring lookalike seeds — e.g. searching
# "OKI TINA" also surfacing OKIEMUTE EKOKIFO (~51) — must not drag their
# own unrelated families into the relationship network. Identifier searches
# (phone/email/TID/MX) are exempt: every row returned genuinely shares the
# queried value, so those families stay intact ("search any fragment, see
# everything about the merchant").
# 85 = the 8.5/10 the user sees on a decisive name hit ("8.5 or 9.0");
# a legitimate multi-entry merchant like MEDPLUS survives because its rows
# share name tokens / TIDs and are therefore still same-merchant seeds.
DECISIVE_MATCH_THRESHOLD = 85

# ── Query-type detection boosts ───────────────────────────────────────────
# When the query looks like a person name (e.g. "NWANERI VICTOR"),
# multiply the contact_name weight by PERSON_NAME_BOOST.
# When the query looks like a bank name (e.g. "MONEYTRUST MICROFINANCE"),
# multiply the account_name weight by BANK_NAME_BOOST.
PERSON_NAME_BOOST = 6.0         # 6x boost for contact_name on person-name queries
BANK_NAME_BOOST   = 6.0         # 6x boost for account_name on bank-name queries

# Tokens commonly found in person names (first names, name markers)
# Used by _is_person_name_query() to detect person-name searches.
PERSON_NAME_MARKERS = {
    # Common Nigerian first names
    "VICTOR", "PETER", "JOHN", "PAUL", "JAMES", "DAVID", "DANIEL",
    "MICHAEL", "SAMUEL", "JOSEPH", "GABRIEL", "PHILIP", "FRANK",
    "ANDREW", "BENJAMIN", "CHRISTOPHER", "ANTHONY", "PATRICK",
    "STEPHEN", "MARK", "MATTHEW", "GEORGE", "ALEX", "LAWRENCE",
    "SOLOMON", "EMMANUEL", "GODWIN", "PROSPER", "HENRY", "FELIX",
    "MARIA", "FAITH", "GRACE", "PEACE", "MERCY", "PRECIOUS",
    "MARY", "HELEN", "ESTHER", "RUTH", "RACHEL", "DEBORAH",
    "VICTORIA", "PRISCILLA", "ELIZABETH", "EUNICE", "DORCAS",
    # Nigerian name particles
    "OLADAPO", "OLUWASEUN", "OLUWASEYI", "OLUWAFEMI", "OLUWATOBI",
    "OLUWADAMILOLA", "CHUKWUMA", "CHUKWUDI", "CHIBUEZE", "CHINEDU",
    "CHIDI", "CHIMA", "NWANERI", "NKECHI", "CHINWE", "CHIOMA",
    "AMAKA", "CHINENYE", "CHIDINMA", "CHIAMAKA", "AMARA",
    "OGOCHUKWU", "DUMESHI", "EBENEZER", "OLADIMEJI", "ADENIKE",
    "FOLASHADE", "BOSE", "ABIODUN", "OLATOYOSI", "MODUPE",
    "HARRISON", "KALEJAIYE", "EZEASOMBA", "NWANERI", "OJO",
    "CHIDI", "ANUCHA", "VICTORIA",
}

# Keywords that indicate a bank/financial institution search
BANK_KEYWORDS = {
    "BANK", "BANKING", "BANQUE", "BANCO",
    "MFB", "M.F.B",
    "MICROFINANCE", "MICRO-FINANCE", "MICRO FINANCE",
    "BANK LTD", "BANK LIMITED",
    "SETTLEMENT", "REVENUE COLLECTION", "COLLECTION ACCOUNT",
    "MICROFINANACE",  # common misspelling in the workbook
}

# ── Query noise stripping (natural-language hygiene) ──────────────────────
# Users paste instructions into the search box ("get me all the information
# on medplus"). Without stripping, the noise words (GET, ME, ALL, INFORMATION…)
# become search tokens that pollute scoring and drown the real name.
#
# Two lists, both editable here (no external JSON file):
#   QUERY_NL_TRIGGERS  — phrases that mark a query as an NL request. Stripping
#                        only engages when the query contains one of these,
#                        so a legitimate 2-3 word merchant search like
#                        "ALL SEASONS HOTEL" is never touched (no trigger).
#   QUERY_NOISE_WORDS  — whole tokens removed from the query ONCE a trigger
#                        matched. Stored merchant names are NEVER altered —
#                        this is query-side hygiene only.
QUERY_NL_TRIGGERS = [
    "get", "give", "find", "please", "pls", "show", "need", "want",
    "lookup", "look up", "look", "information", "details", "detail",
    "everything", "info", "tell", "extract", "retrieve", "pull",
    "search for", "give me", "can you", "i need", "i want", "kindly",
    "all information", "all the information", "full profile", "profile",
    # Question words: "what is the TID for X", "which phone is on Y", "how
    # many…". These read unambiguously as requests, so stripping may engage.
    "what", "which", "how", "where", "who", "when", "whose", "why",
]

QUERY_NOISE_WORDS = {
    "get", "me", "all", "the", "information", "info", "details",
    "detail", "please", "pls", "show", "give", "find", "lookup",
    "look", "up", "want", "need", "about", "everything", "full",
    "profile", "any", "each", "of", "on", "for", "to", "with",
    "and", "a", "an", "can", "you", "i", "my", "do", "tell",
    "extract", "retrieve", "pull", "search", "us", "their", "them",
    "which", "what", "where", "how", "who", "will", "kindly",
    # NOTE: field words (TID, email, phone, bank, account, MX code…) are
    # deliberately NOT in this set. They legitimately appear inside merchant
    # names ("FIRST BANK", "ACCESS BANK", "MEDPLUS PHARMACY") — a global
    # strip would break those searches. Field words are handled positionally
    # by matcher.strip_query_noise()'s field-request pattern instead, which
    # only removes them when they read as the OUTPUT of a request
    # ("get me the TID for X", "show me the email of Y").
}

# ── Token matching (Phase 3) ───────────────────────────────────────────────
MIN_TOKEN_LENGTH = 3           # ignore tokens shorter than this

# ── Compound word splitting (Phase 3b) ──────────────────────────────────────
# Known prefixes that commonly start compound merchant tokens (e.g. POWER in POWERFOIL)
# Editable externally via data/known_compounds.json ({"prefixes": [...], "suffixes": [...]})
_DEFAULT_KNOWN_PREFIXES = {
    "POWER", "MULTI", "MICRO", "FINANCE", "PHARMA", "HEALTH",
    "ENTERPRISE", "SERVICE", "INVEST", "RESOURCE", "PETROLEUM",
    "NIGERIA", "INTERNATIONAL", "BEACON", "MONEY",
    "MANAGEMENT", "CONSULTING", "TECHNOLOGY", "SOLUTION",
    "PROPERTY", "REALTY", "ESTATE", "CAPITAL", "HOLDING",
    "TECHNICAL", "SECURITY", "LOGISTICS", "TRANSPORT",
    "COMMUNICATION", "DEVELOPMENT", "CONSTRUCTION",
    "NEW", "GOLD", "AUTO", "SUPER", "GRAND",
    "PREMIER", "PRIME", "ROYAL", "BLUE", "GREEN",
    "SMART", "FIRST", "TOP", "WATER", "TRANS",
}

# Known suffixes that commonly end compound merchant tokens (e.g. FOIL in POWERFOIL)
_DEFAULT_KNOWN_SUFFIXES = {
    "FOIL", "CARE", "TECH", "SOLUTIONS", "SERVICES", "GROUP",
    "HOUSE", "LINE", "WORKS", "POINT", "ZONE", "LINK",
    "MARKET", "PLACE", "WORLD", "SYSTEMS", "NET", "SOFT",
    "WAY", "CITY", "PARK", "DALE", "FIELD", "BRIDGE",
    "HEALTH", "PHARMA",
    "MART", "PLAZA", "PLUS", "VIEW",
    "CARE", "BANK", "STORE", "SHOP",
    "SIDE", "FRONT", "CENTRE", "CENTER",
    "MAX", "PRO", "WATER",
}


def _load_compound_lists() -> tuple:
    """Load data/known_compounds.json ({"prefixes": [...], "suffixes": [...]}) if
    present, else fall back to the built-in defaults."""
    try:
        path = DATA_DIR / "known_compounds.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            prefixes = set(data.get("prefixes", _DEFAULT_KNOWN_PREFIXES))
            suffixes = set(data.get("suffixes", _DEFAULT_KNOWN_SUFFIXES))
            if prefixes or suffixes:
                return prefixes, suffixes
    except Exception:
        pass
    return set(_DEFAULT_KNOWN_PREFIXES), set(_DEFAULT_KNOWN_SUFFIXES)


KNOWN_PREFIXES, KNOWN_SUFFIXES = _load_compound_lists()

# ── Name abbreviations (normalization layer) ────────────────────────────────
# Expanded by fuzzy.canonicalize() at both ingest and query time, so stored
# "INT'L" and queried "INTERNATIONAL" become the same token.
NAME_ABBREVIATIONS = {
    "INT'L": "INTERNATIONAL",
    "INTL": "INTERNATIONAL",
    "INT L": "INTERNATIONAL",
    "CO LTD": "COMPANY LIMITED",
    "BROS": "BROTHERS",
}

# ── Known typo corrections (normalization layer) ────────────────────────────
# Recurring misspellings found in the source workbooks (and the queries users
# actually type). fuzzy.canonicalize() rewrites them at both ingest and query
# time, so a stored "MONEYTRUST MICROFINANACE BANK LTD" and a queried
# "MONEYTRUST MICROFINANCE BANK" tokenise to the same tokens and map to the
# same bucket key — no fuzzy luck, no aliases needed.
#
# Editable externally via data/typo_fixes.json ({"fixes": {"typo": "correct"}})
_DEFAULT_TYPO_FIXES = {
    # Misspellings confirmed in the actual workbooks
    "MICROFINANACE": "MICROFINANCE",   # MONEYTRUST MICROFINANACE BANK LTD
    "MICROFINACE": "MICROFINANCE",
    "LIIMITED": "LIMITED",             # POWERFOIL GLOBAL SERVICES LIIMITED
    "INTERNMATIONAL": "INTERNATIONAL", # CRANE FIELD INTERNMATIONAL SCHOOL
    "OLWADAMS": "OLUWADAMS",           # OLWADAMS PETROLEUM (query-side typo)
    # Common general misspellings that keep appearing across sheets
    "ENTERPRIZE": "ENTERPRISE",
    "ENTERPRIZES": "ENTERPRISES",
    "SERVCIES": "SERVICES",
    "BUSINESSS": "BUSINESS",
    "COMPNAY": "COMPANY",
    "HOTELL": "HOTEL",
    "RESTURANT": "RESTAURANT",
    "NIGERAI": "NIGERIA",
}


def _load_typo_fixes() -> dict:
    """Load data/typo_fixes.json ({"fixes": {...}}) if present, else defaults.

    The external file is the source of truth when it exists — you can teach
    the engine new typo corrections without touching Python.
    """
    try:
        path = DATA_DIR / "typo_fixes.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            fixes = data.get("fixes", {})
            if isinstance(fixes, dict) and fixes:
                return fixes
    except Exception:
        pass
    return dict(_DEFAULT_TYPO_FIXES)


TYPO_FIXES = _load_typo_fixes()

# ── NIBSS bank codes → bank names ────────────────────────────────────────
# The `bank` column in the workbooks stores NIBSS institution codes (070, 058,
# 011…), NOT human-readable bank names. This map resolves a stored code to its
# bank name so the UI shows "Fidelity Bank" instead of "070".
# Sources: NIBSS / CBN licensed-bank codes used across the parameter files.
NIBSS_BANKS = {
    "011": "First Bank of Nigeria",
    "023": "Citibank Nigeria",
    "030": "Heritage Bank",
    "032": "Union Bank of Nigeria",
    "033": "United Bank for Africa (UBA)",
    "035": "Wema Bank",
    "040": "Ecobank Nigeria",
    "044": "Access Bank",
    "050": "Ecobank Nigeria",
    "058": "Guaranty Trust Bank (GTBank)",
    "063": "Diamond Bank",
    "068": "Standard Chartered Bank",
    "070": "Fidelity Bank",
    "076": "Polaris Bank",
    "082": "Keystone Bank",
    "084": "Enterprise Bank",
    "085": "SunTrust Bank",
    "090": "Providus Bank",
    "101": "Providus Bank",
    "103": "Globus Bank",
    "214": "First City Monument Bank (FCMB)",
    "215": "Unity Bank",
    "221": "Stanbic IBTC Bank",
    "232": "Sterling Bank",
    "301": "Jaiz Bank",
    "302": "Kuda Microfinance Bank",
    "303": "Moniepoint MFB",
    "401": "Mint FB",
    "501": "9 Payment Service Bank (9PSB)",
    "502": "Eyowo MFB",
    "503": "Paga",
    "505": "Grey MFB",
    "901": "9 Payment Service Bank (9PSB)",
    "903": "Palmpay",
    "904": "Opay",
    "905": "Moniepoint MFB",
    "999": "CBN Settlement",
}


def bank_name(code: str) -> str:
    """Resolve a stored NIBSS bank code to a human-readable bank name.

    Returns the original value unchanged when it's not a known code (e.g. a
    genuine bank name already present, or junk).
    """
    s = str(code or "").strip()
    if not s:
        return ""
    # Already a name (has letters) — pass through
    if any(ch.isalpha() for ch in s):
        return s
    return NIBSS_BANKS.get(s, s)


# ── DB batch size for bulk inserts ─────────────────────────────────────────
DB_BATCH_SIZE = 500

# ── SQLite FTS configuration ──────────────────────────────────────────────
FTS_TOKENIZER = "unicode61"   # options: unicode61, porter, trigram
