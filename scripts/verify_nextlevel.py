"""verify_nextlevel.py — End-to-end verification of the next-level build."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import MerchantSearch, config
from merchant_intelligence.entity import EntityResolver

print("=" * 70)
print("  1. TYPO-TOLERANT + TRIGRAM SEARCH")
print("=" * 70)
s = MerchantSearch()
for q in ["POWERFOIL GLOBAL SERVICES LIIMITED",
          "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
          "BEACONHEALTH DIAGNOSTICS"]:
    print(f"\nQuery: {q}")
    results = s.search(q, limit=5, min_score=0)
    if results:
        for res in results[:5]:
            name = res.record.get("merchant_name", "")
            score = round(res.overall_score / 10, 1)
            print(f"  {score:5.1f}/10  {name[:60]}")
    else:
        print("  (no results)")

print("\n" + "=" * 70)
print("  2. ENTITY RESOLUTION: LAGOON WATERS")
print("=" * 70)
er = EntityResolver()
fam = er.family_of("LAGOON WATERS")
members = fam.get("members", [])
print(f"Linked records: {len(members)}")
for m in members[:15]:
    name = str(m.get("merchant_name", ""))[:50]
    mx = str(m.get("mxcode", ""))[:10]
    email = str(m.get("email", ""))[:35]
    print(f"  {name:50s} | mx={mx:10s} | {email}")
print(f"\nAlias candidates: {fam.get('alias_candidates', [])[:10]}")

print("\n" + "=" * 70)
print("  3. ENTITY RESOLUTION: MONEYTRUST (account-name link check)")
print("=" * 70)
fam2 = er.family_of("MONEYTRUST MICROFINANACE BANK LTD")
members2 = fam2.get("members", [])
print(f"Linked records: {len(members2)}")
for m in members2[:10]:
    print(f"  {str(m.get('merchant_name', ''))[:50]:50s} | {str(m.get('account_name', ''))[:40]}")

print("\n" + "=" * 70)
print("  4. TRIGRAM INDEX PRESENT IN BOTH DBs")
print("=" * 70)
for dbp in [config.DB_FILE, config.DB_DIR / "merchant_intel.db"]:
    from merchant_intelligence.database import DatabaseManager
    dm = DatabaseManager(dbp)
    print(f"  {dbp.name:<25} trigram={dm.has_trigram_index()}")

print("\nDONE — all verification steps complete.")
