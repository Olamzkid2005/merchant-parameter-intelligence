"""Test the identifier search feature (phone/email/TID/MX/account) end-to-end."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merchant_intelligence import MerchantSearch


def show(label, q, limit=3):
    t0 = time.perf_counter()
    rs = s.search(q, limit=limit)
    dt = time.perf_counter() - t0
    print(f"=== {label}: {q!r} ({dt:.2f}s, {len(rs)} results) ===")
    for r in rs:
        rec = r.record
        print(
            f"  {r.overall_score:6.1f} {r.match_type:<16} "
            f"{str(rec.get('merchant_name', ''))[:38]} | hit={r.identifier_hit or '-'} "
            f"| phone={rec.get('phone', '')} | tid={rec.get('tid', '')} | mx={rec.get('mxcode', '')}"
        )


s = MerchantSearch()

# Identifier searches — must stay 98.0 Exact Match
show("phone 0-prefix", "08098726020")
show("phone +234", "2348098726020")
show("phone +234 with +", "+2348098726020")
show("phone no-leading-zero", "8098726020")
show("MX code", "MX183544")
show("TID", "2103O166")
show("email", "smonsuru@filmhouseng.com")

# Name-search regressions (must stay fast and correct)
show("name regression", "THE FILM HOUSE")
show("name regression 2", "LAGOON WATERS")
show("name regression 3", "POWERFOIL")

# Edge cases from review
# 1. Person-name queries must NOT be lifted to High Confidence via a code
#    field substring match (e.g. VICTOR inside a merchant_id/TID).
show("person name (no false code hit)", "VICTOR", limit=5)
# 2. Short digit query must NOT flood with 90-scoring substring hits.
show("short digit query (no flood)", "080", limit=5)
show("short digit query 2 (no flood)", "123", limit=5)

# A query that is neither identifier nor name — should not blow up
show("garbage query", "zzqqxxyy")

# ── Confusable-character search (0↔O, 1↔I, 2↔Z, 5↔S, 8↔B) ─────────────────
# WSV VENTURES is stored with TID 2103O265 (letter O). Searching the digit-0
# spelling 21030265 must find it — the DB is the ground truth, so the
# confusable variant only matches because the registry stores that form.
print("\n=== confusable search (digit-0 vs letter-O TID) ===")
_conf_rs = s.search("21030265", limit=5)
print(f"21030265 -> {len(_conf_rs)} results")
_conf_found = any(
    str(r.record.get("merchant_name", "")).upper().startswith("WSV")
    for r in _conf_rs
)
print("  found WSV VENTURES:", _conf_found)
assert _conf_found, "digit-0 TID 21030265 must resolve WSV VENTURES (stored 2103O265)"
# The reverse direction: letter-O query finds it directly (already worked,
# but assert it stays exact-match).
_conf_rs2 = s.search("2103O265", limit=5)
print(f"2103O265 -> {len(_conf_rs2)} results")
assert _conf_rs2 and _conf_rs2[0].overall_score >= 95, \
    f"letter-O TID must stay exact-match, got {_conf_rs2[0].overall_score}"
# Exact stored value still outranks confusable-typed value (never inverted).
_exact = next(r for r in _conf_rs2 if r.identifier_hit == "tid")
_conf = next((r for r in _conf_rs if r.identifier_hit == "tid"), None)
print("  exact score:", _exact.overall_score, "| confusable score:", _conf and _conf.overall_score)
assert _exact.overall_score >= (_conf.overall_score if _conf else 0), \
    "exact spelling must not score below a confusable spelling"
print("CONFUSABLE SEARCH OK")
