"""test_autocomplete.py — End-to-end check of the autocomplete endpoint.

Verifies the DatabaseManager.autocomplete + GET /api/autocomplete wiring:
  1. ensure_buckets() builds lazily on an existing database
  2. autocomplete returns canonical bucket keys for a real prefix
  3. the API handler returns the expected {prefix, suggestions} shape
  4. empty prefix returns an empty list (no crash)
  5. typo-tolerant prefix tier: a typo'd prefix ('medpluz') still suggests
     the right bucket ('MEDPLUS') via the Damerau-Levenshtein prefix scan,
     and garbage prefixes stay empty
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


# ── 1. Direct DatabaseManager path ────────────────────────────────────────
print("[1] DatabaseManager.autocomplete")
from merchant_intelligence.database import DatabaseManager

db = DatabaseManager()
db.ensure_buckets()  # lazy build on the active DB
check("ensure_buckets succeeded", db.has_buckets())

ac = db.autocomplete("LAGOON")
check("'LAGOON' returns suggestions", len(ac) > 0, f"{ac}")
check("suggestions are uppercase canonical keys",
      all(s == s.upper() for s in ac), f"{ac}")

ac2 = db.autocomplete("lagoon wat")  # canonicalized prefix
check("lowercase partial 'lagoon wat' still hits", len(ac2) > 0, f"{ac2}")

ac3 = db.autocomplete("MX")
check("'MX' (code prefix) returns something or empty gracefully",
      isinstance(ac3, list))

# ── 2. API handler shape ──────────────────────────────────────────────────
print("[2] /api/autocomplete handler shape")
import api as api_module

res = api_module.autocomplete(prefix="LAGOON", limit=5)
check("returns dict with prefix + suggestions",
      isinstance(res, dict) and "suggestions" in res, f"{list(res.keys())}")
check("suggestions is a list", isinstance(res.get("suggestions"), list))
check("LAGOON suggestions non-empty", len(res.get("suggestions", [])) > 0)

res_empty = api_module.autocomplete(prefix="", limit=5)
check("empty prefix -> empty suggestions",
      res_empty == {"prefix": "", "suggestions": []}, f"{res_empty}")

res_short = api_module.autocomplete(prefix="A", limit=3)
check("short prefix returns list (no crash)", isinstance(res_short["suggestions"], list))

res_bad = api_module.autocomplete(prefix="ZZZZNOTREAL", limit=5)
check("garbage prefix -> empty list", res_bad["suggestions"] == [], f"{res_bad}")

# ── 3. Typo-tolerant prefix tier ───────────────────────────────────────────
print("[3] typo-tolerant prefix tier (Damerau-Levenshtein)")

# Single-token typo that the token-based tier cannot see: 'MEDPLUZ' is one
# unbroken string, so token_set_ratio sees no shared tokens — the char-level
# prefix scan must recover MEDPLUS.
res_typo = api_module.autocomplete(prefix="MEDPLUZ", limit=5)
check("'MEDPLUZ' suggests MEDPLUS",
      any("MEDPLUS" in s for s in res_typo["suggestions"]), f"{res_typo}")

res_typo2 = api_module.autocomplete(prefix="KONGOPAY", limit=5)
check("'KONGOPAY' suggests KONGAPAY",
      any("KONGAPAY" in s for s in res_typo2["suggestions"]), f"{res_typo2}")

# Transposition is the whole point of Damerau-Levenshtein (ONE edit):
# 'MEDLPUS' swaps two adjacent letters and must still recover MEDPLUS.
res_typo_t = api_module.autocomplete(prefix="MEDLPUS", limit=5)
check("'MEDLPUS' (transposition) suggests MEDPLUS",
      any("MEDPLUS" in s for s in res_typo_t["suggestions"]), f"{res_typo_t}")

# Multi-word typo prefix still recovers through the char-level tier.
res_typo3 = api_module.autocomplete(prefix="lagoon watr", limit=5)
check("'lagoon watr' suggests LAGOON WATERS",
      any("LAGOON WATERS" in s for s in res_typo3["suggestions"]), f"{res_typo3}")

# A plausible-but-wrong prefix must NOT match: 1-edit lookalikes of real
# keys are fine, but a nonsense prefix edits to nothing in range.
res_garb = api_module.autocomplete(prefix="WXYZZQRST", limit=5)
check("nonsense prefix stays empty", res_garb["suggestions"] == [], f"{res_garb}")

# A mid-name-only edit match must NOT surface (the prefix scan only honours
# matches at the START of a key).
res_mid = api_module.autocomplete(prefix="XYZZ", limit=5)
check("mid-name lookalike stays empty", res_mid["suggestions"] == [], f"{res_mid}")

print("\n" + "=" * 60)
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
