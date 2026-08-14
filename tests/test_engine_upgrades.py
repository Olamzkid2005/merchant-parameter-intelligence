"""
test_engine_upgrades.py — Tests for the six core-engine upgrades.

Covers:
  1. canonicalize() normalization layer (G&G, INT'L, E'SORAE, &)
  2. Damerau-Levenshtein transposition-aware matching
  3. Coverage penalty (multi-token queries that match only one token)
  4. Precomputed name buckets (instant lookup + autocomplete)
  5. Token-stat cache (compound expansion stops re-querying the DB)
  6. Config loaded from external JSON data files

Run:  python test_engine_upgrades.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import config
from merchant_intelligence.database import DatabaseManager, build_name_buckets
from merchant_intelligence.fuzzy import (canonicalize,
                                         damerau_levenshtein_similarity,
                                         levenshtein_similarity)
from merchant_intelligence.matcher import MerchantMatcher, SearchResult

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


# ── 1. Normalization layer ────────────────────────────────────────────────
print("\n[1] canonicalize() normalization layer")
check("G&G -> 'G AND G'", canonicalize("G&G") == "G AND G",
      repr(canonicalize("G&G")))
check("G & G -> 'G AND G'", canonicalize("G & G") == "G AND G",
      repr(canonicalize("G & G")))
check("INT'L -> INTERNATIONAL",
      "INTERNATIONAL" in canonicalize("TEGRA-EAGLES CONCEPT INT'L LTD"))
check("E'SORAE -> ESORAE", "ESORAE" in canonicalize("E'SORAE HOME STORES"),
      repr(canonicalize("E'SORAE HOME STORES")))
check("lowercase -> uppercase", canonicalize("lagoon waters") == "LAGOON WATERS")
check("punctuation collapsed",
      canonicalize("FILMHOUSE CINEMA - CIRCLE MALL") == "FILMHOUSE CINEMA CIRCLE MALL")
# ── 1b. Typo normalization layer ────────────────────────────────────────────
print("\n[1b] typo_fixes normalization layer")
check("MICROFINANACE -> MICROFINANCE",
      "MICROFINANCE" in canonicalize("MONEYTRUST MICROFINANACE BANK LTD"))
check("LIIMITED -> LIMITED",
      "LIMITED" in canonicalize("POWERFOIL GLOBAL SERVICES LIIMITED"))
check("INTERNMATIONAL -> INTERNATIONAL",
      "INTERNATIONAL" in canonicalize("CRANE FIELD INTERNMATIONAL SCHOOL"))
check("OLWADAMS -> OLUWADAMS",
      canonicalize("OLWADAMS PETROLEUM") == canonicalize("OLUWADAMS PETROLEUM"))
check("TYPO_FIXES defined + loads from config",
      "MICROFINANACE" in config.TYPO_FIXES and
      config.TYPO_FIXES["MICROFINANACE"] == "MICROFINANCE")
check("no false rewrite (word boundary respected)",
      canonicalize("UNLIMITED CAPITAL") == "UNLIMITED CAPITAL",
      repr(canonicalize("UNLIMITED CAPITAL")))
# LIIMITED appears INSIDE UNLIIMITED but not at a word boundary — the
# boundary-aware regex must leave it alone.
check("typo inside longer word not rewritten",
      canonicalize("UNLIIMITED") == "UNLIIMITED",
      repr(canonicalize("UNLIIMITED")))

# ── 2. Damerau-Levenshtein ────────────────────────────────────────────────
print("\n[2] Damerau-Levenshtein (transpositions = 1 edit)")
# A TRUE transposition (last two letters swapped): Damerau counts 1 edit,
# plain Levenshtein counts 2 — so damerau > levenshtein.
d = damerau_levenshtein_similarity("INTERNATIONLA", "INTERNATIONAL")
l = levenshtein_similarity("INTERNATIONLA", "INTERNATIONAL")
check("INTERNATIONLA (transposition): damerau > levenshtein",
      d > l, f"dam={d:.3f} lev={l:.3f}")
# INSERTION typos (the INTERNMATIONAL / MICROFINANACE class) cost 1 edit in
# BOTH metrics — damerau must be >= levenshtein, never worse.
d3 = damerau_levenshtein_similarity("INTERNMATIONAL", "INTERNATIONAL")
l3 = levenshtein_similarity("INTERNMATIONAL", "INTERNATIONAL")
check("INTERNMATIONAL (insertion): damerau >= levenshtein",
      d3 >= l3, f"dam={d3:.3f} lev={l3:.3f}")
d2 = damerau_levenshtein_similarity("MICROFINANACE", "MICROFINANCE")
check("MICROFINANACE vs MICROFINANCE: damerau high", d2 > 0.9, f"{d2:.3f}")
check("damerau exact = 1.0",
      damerau_levenshtein_similarity("POWERFOIL", "POWERFOIL") == 1.0)
check("damerau unrelated low",
      damerau_levenshtein_similarity("FIELD", "OCEAN") < 0.3,
      f"{damerau_levenshtein_similarity('FIELD', 'OCEAN'):.3f}")

# ── 3. Coverage penalty (matcher scoring) ─────────────────────────────────
print("\n[3] Coverage penalty - single-token match on multi-token query")
# Build a tiny in-memory DB to exercise the real matcher path.
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
conn = sqlite3.connect(tmp.name)
conn.executescript("""
CREATE TABLE merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_name TEXT, row_number INTEGER, merchant_name TEXT,
    merchant_id TEXT, mxcode TEXT, payable_code TEXT, tid TEXT,
    terminal_serial TEXT, slip_header TEXT, email TEXT, phone TEXT,
    address TEXT, contact_name TEXT, contact_title TEXT, account_name TEXT,
    account_number TEXT, bank TEXT, state TEXT, state_code TEXT,
    bvn TEXT, ptsp TEXT, terminal_type TEXT, deployment_status TEXT,
    alias TEXT, static_acc_no TEXT, remarks TEXT, raw_data TEXT,
    imported_at TEXT
);
""")
rows = [
    (1, "FIELD AND OCEAN", "", ""),                      # false-positive target
    (2, "FIELD AND OCEAN LEKKI", "", ""),
    (3, "CRANE FIELD SCHOOL JEDDO", "", ""),             # the true record
    (4, "POWERFOIL GLOBAL SERVICES LIMITED", "", ""),
]
for rid, name, slip, acct in rows:
    conn.execute(
        "INSERT INTO merchants (id, sheet_name, row_number, merchant_name,"
        " slip_header, account_name) VALUES (?,?,?,?,?,?)",
        (rid, "t", rid + 1, name, slip, acct))
conn.commit()

db = DatabaseManager(tmp.name)
matcher = MerchantMatcher(db)

# Directly verify the coverage penalty mechanism on the merchant_name field:
# 4-token query where only 1 token matches -> merchant_name scaled down.
row1 = {"id": 1, "merchant_name": "FIELD AND OCEAN"}
q_tokens = matcher._tokenise("CRANE FIELD SCHOOL JEDDO")
r1 = matcher._score_row(row1, q_tokens, "CRANE FIELD SCHOOL JEDDO")
# Sanity: 1 of 4 query tokens matched -> qcov 0.25 -> factor 0.55
check("coverage penalty applied to merchant_name",
      r1.field_scores.get("merchant_name", 0.0) <= 60.0,
      f"score={r1.field_scores.get('merchant_name')}")
# Unpenalized control: same tokens, merchant matching ALL 4 -> no penalty.
row3 = {"id": 3, "merchant_name": "CRANE FIELD SCHOOL JEDDO"}
r3 = matcher._score_row(row3, q_tokens, "CRANE FIELD SCHOOL JEDDO")
check("full match not penalized (higher than partial)",
      r3.overall_score > r1.overall_score,
      f"full={r3.overall_score} partial={r1.overall_score}")
# Short queries (1-2 tokens) must NOT be penalized — FILM HOUSE style.
row4 = {"id": 4, "merchant_name": "POWERFOIL GLOBAL SERVICES LIMITED"}
r4 = matcher._score_row(row4, matcher._tokenise("POWERFOIL"), "POWERFOIL")
check("short query POWERFOIL not penalized",
      r4.field_scores.get("merchant_name", 0.0) >= 80.0,
      f"score={r4.field_scores.get('merchant_name')}")

# ── 4. Precomputed name buckets ───────────────────────────────────────────
print("\n[4] Precomputed name buckets (instant lookup + autocomplete)")
build_name_buckets(conn)
db._has_buckets = None  # reset cache so it sees the new table
# lookups take the CANONICAL bucket key (generics stripped)
hits = db.lookup_bucket(DatabaseManager._bucket_key("FIELD AND OCEAN"))
check("bucket lookup 'FIELD AND OCEAN' finds rows", len(hits) >= 1,
      f"{len(hits)} rows")
pf_key = DatabaseManager._bucket_key("POWERFOIL GLOBAL SERVICES LIMITED")
pf_hits = db.lookup_bucket(pf_key)
check("bucket keys are normalized (generics stripped)",
      pf_key == "POWERFOIL" and len(pf_hits) >= 1,
      f"key={pf_key!r} rows={len(pf_hits)}")
ac = db.autocomplete("LAGOON")
check("autocomplete 'LAGOON' returns suggestions (or empty gracefully)",
      isinstance(ac, list))
ac2 = db.autocomplete("POWER")
check("autocomplete 'POWER' -> POWERFOIL bucket",
      any("POWERFOIL" in k for k in ac2), f"{ac2}")

# ── 5. Token-stat cache ───────────────────────────────────────────────────
print("\n[5] Token-stat cache (compound expansion)")
matcher2 = MerchantMatcher(db)
matcher2._token_db_count("POWER")
matcher2._token_exists_in_db("POWER")
check("token stats cached in _token_stats",
      "POWER" in matcher2._token_stats, f"{list(matcher2._token_stats)[:5]}")
c1 = matcher2._token_db_count("POWER")
c2 = matcher2._token_db_count("POWER")
check("cached count stable", c1 == c2, f"{c1} vs {c2}")
check("compound expansion uses cache (no crash, returns list)",
      isinstance(matcher2._expand_compound_tokens(["POWERFOIL"]), list))

# ── 6. Config from external JSON files ────────────────────────────────────
print("\n[6] Config loaded from external JSON data files")
check("MANUAL_ALIASES has THE FILM HOUSE LIMITED",
      "THE FILM HOUSE LIMITED" in {k.upper() for k in config.MANUAL_ALIASES})
check("KNOWN_PREFIXES includes POWER", "POWER" in config.KNOWN_PREFIXES)
check("KNOWN_SUFFIXES includes FOIL", "FOIL" in config.KNOWN_SUFFIXES)
check("NAME_ABBREVIATIONS defined",
      "INT'L" in config.NAME_ABBREVIATIONS)
typo_file = config.DATA_DIR / "typo_fixes.json"
if typo_file.exists():
    check("typo_fixes.json exists + loads",
          "MICROFINANACE" in config.TYPO_FIXES)
else:
    check("typo_fixes.json optional (built-in defaults used)",
          "MICROFINANACE" in config.TYPO_FIXES)
aliases_file = config.DATA_DIR / "manual_aliases.json"
compounds_file = config.DATA_DIR / "known_compounds.json"
check("manual_aliases.json exists", aliases_file.exists())
check("known_compounds.json exists", compounds_file.exists())

conn.close()
try:
    Path(tmp.name).unlink()
except OSError:
    pass

print("\n" + "=" * 60)
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
