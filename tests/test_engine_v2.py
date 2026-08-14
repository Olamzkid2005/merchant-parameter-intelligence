"""
test_engine_v2.py — Tests for the v2 core-engine upgrades.

Covers:
  1. Diacritic stripping in canonicalize (ẸBENEZER -> EBENEZER)
  2. NUBAN / BVN / TID identifier-format validation
  3. Better ratios: token_set_ratio (subset-tolerant)
  4. Dilution fix: only fields with real signal contribute to the weighted
     average — an exact name-only match now scores >= 95 (was ~65)
  5. IDF weighting: rarer tokens carry more evidence
  6. Fuzzy bucket-key pass: a typo'd query recovers the near-exact bucket

Run:  python tests/test_engine_v2.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The test prints accented characters (ẸBENEZER) — force UTF-8 output so a
# Windows cp1252 console can't crash the suite.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from merchant_intelligence import config
from merchant_intelligence.database import DatabaseManager, build_name_buckets
from merchant_intelligence.fuzzy import (canonicalize, fuzzy_ratio,
                                         is_plausible_tid, is_valid_bvn,
                                         is_valid_nuban, token_set_ratio)
from merchant_intelligence.matcher import MerchantMatcher

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


def _valid_nuban(first9: str) -> str:
    """Construct a NUBAN-valid 10-digit account from a 9-digit prefix."""
    weights = (3, 7, 3, 3, 7, 3, 3, 7, 3)
    total = sum(int(d) * w for d, w in zip(first9, weights))
    check_digit = (10 - (total % 10)) % 10
    return first9 + str(check_digit)


# ── 1. Diacritics ─────────────────────────────────────────────────────────
print("\n[1] Diacritic normalization")
check("ẸBENEZER -> EBENEZER", canonicalize("ẸBENEZER OJO") == "EBENEZER OJO",
      repr(canonicalize("ẸBENEZER OJO")))
check("ỌLÁYINKA -> OLAYINKA", canonicalize("ỌLÁYINKA") == "OLAYINKA",
      repr(canonicalize("ỌLÁYINKA")))
check("ṢADE -> SADE", canonicalize("ṢADE") == "SADE")
check("accented == plain", canonicalize("ẸBENEZER") == canonicalize("EBENEZER"))
check("NÀIJA -> NAIJA", canonicalize("NÀIJA") == "NAIJA")

# ── 2. Identifier format validation ───────────────────────────────────────
print("\n[2] NUBAN / BVN / TID validation")
ok_acct = _valid_nuban("012345678")
check("valid NUBAN accepted", is_valid_nuban(ok_acct), f"({ok_acct})")
check("sequential junk rejected", not is_valid_nuban("0123456789"))
check("9-digit rejected", not is_valid_nuban("012345678"))
check("non-numeric rejected", not is_valid_nuban("ABCDEFGHIJ"))
check("valid BVN accepted", is_valid_bvn("22345678901"))
check("BVN not starting with 2 rejected", not is_valid_bvn("12345678901"))
check("short BVN rejected", not is_valid_bvn("2345678901"))
check("TID 21030173 plausible", is_plausible_tid("21030173"))
check("TID 2103O338 plausible", is_plausible_tid("2103O338"))
check("TID 507 not plausible", not is_plausible_tid("507"))

# ── 3. Better ratios ──────────────────────────────────────────────────────
print("\n[3] token_set_ratio (subset-tolerant)")
tsr = token_set_ratio("LAGOON WATERS", "LAGOON WATER ENT")
r = fuzzy_ratio("LAGOON WATERS", "LAGOON WATER ENT")
check("token_set >= plain ratio for subset names", tsr >= r, f"tsr={tsr:.2f} r={r:.2f}")
check("subset names score high", tsr > 0.8, f"tsr={tsr:.2f}")
check("unrelated names low", token_set_ratio("FIELD", "OCEAN") < 0.3)

# ── 4-6. Matcher against a synthetic DB ───────────────────────────────────
print("\n[4-6] Dilution fix / IDF / fuzzy buckets (synthetic DB)")
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
    (1, "LAGOON WATERS", "", ""),
    (2, "LAGOON WATERS LTD", "", ""),
    (3, "FIELD AND OCEAN", "", ""),
    (4, "CRANE FIELD SCHOOL JEDDO", "", ""),
    (5, "POWERFOIL GLOBAL SERVICES LIMITED", "", ""),
]
for rid, name, slip, acct in rows:
    conn.execute(
        "INSERT INTO merchants (id, sheet_name, row_number, merchant_name,"
        " slip_header, account_name) VALUES (?,?,?,?,?,?)",
        (rid, "t", rid + 1, name, slip, acct))
conn.commit()

db = DatabaseManager(tmp.name)
matcher = MerchantMatcher(db)

# 4. Dilution fix — exact normalized name match must now score >= 95
r1 = matcher._score_row({"id": 1, "merchant_name": "LAGOON WATERS"},
                        matcher._tokenise("LAGOON WATERS"), "LAGOON WATERS")
check("exact name-only match >= 95 (dilution fixed)", r1.overall_score >= 95,
      f"score={r1.overall_score}")
check("exact name-only match classified Exact", r1.match_type == "Exact Match",
      r1.match_type)
r2 = matcher._score_row({"id": 2, "merchant_name": "LAGOON WATERS LTD"},
                        matcher._tokenise("LAGOON WATERS"), "LAGOON WATERS")
check("name substring (LTD extra) >= 85", r2.overall_score >= 85,
      f"score={r2.overall_score}")

# 5. IDF — rare tokens outrank common ones
idf_common = matcher._idf("FIELD")
idf_rare = matcher._idf("SUNBEAMZ")
check("rare token has higher IDF than common", idf_rare > idf_common,
      f"rare={idf_rare:.2f} common={idf_common:.2f}")

# 6. Fuzzy bucket-key pass — typo'd query recovers the near-exact bucket
build_name_buckets(conn)
db._has_buckets = None
db._bucket_keys = None  # force re-read of the freshly built table
res = matcher.search("POWERFOL", limit=5, min_score=0)
top = res[0] if res else None
check("typo'd 'POWERFOL' finds POWERFOIL bucket",
      top is not None and "POWERFOIL" in str(top.record.get("merchant_name", "")),
      f"top={top.record.get('merchant_name') if top else None} score={top.overall_score if top else 0}")

# Coverage penalty still separates the true record from a common-word match
qt = matcher._tokenise("CRANE FIELD SCHOOL JEDDO")
fp = matcher._score_row({"id": 3, "merchant_name": "FIELD AND OCEAN"}, qt,
                        "CRANE FIELD SCHOOL JEDDO")
true = matcher._score_row({"id": 4, "merchant_name": "CRANE FIELD SCHOOL JEDDO"}, qt,
                          "CRANE FIELD SCHOOL JEDDO")
check("coverage penalty: true record outranks common-word match",
      true.overall_score > fp.overall_score,
      f"true={true.overall_score} fp={fp.overall_score}")
check("true record scored highly", true.overall_score >= 90,
      f"score={true.overall_score}")

# Identifier plausibility gate on the matcher itself
check("matcher rejects implausible TID substring",
      MerchantMatcher._plausible_identifier("tid", "507") is False)
check("matcher accepts plausible account NUBAN",
      MerchantMatcher._plausible_identifier("account_number", ok_acct) is True)

# Dilution fallback: a junk NAME query that only grazes a non-name field
# (tid '12345') must keep the OLD all-weights denominator — no name-bearing
# field matched, no identifier hit, so the score stays low.
row_junk = {"id": 6, "merchant_name": "MAMA TEE VENTURES", "tid": "1234567890"}
rj = matcher._score_row(row_junk, matcher._tokenise("ZZ FAKE CORP 12345"),
                        "ZZ FAKE CORP 12345")
check("junk query grazing a tid stays low (dilution fallback)",
      rj.overall_score < 50, f"score={rj.overall_score}")

conn.close()
try:
    Path(tmp.name).unlink()
except OSError:
    pass

print("\n" + "=" * 60)
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
