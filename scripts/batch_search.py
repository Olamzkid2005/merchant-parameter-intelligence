"""
Quick batch search all 33 merchants using only regular search (faster).
Then do targeted token breakdowns on the not-found ones.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence.search import MerchantSearch

MERCHANTS = [
    "ADDIDE OGBA",
    "A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)",
    "Artee Industries Limited",
    "ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)",
    "BEACONHEALTH DIAGNOSTICS",
    "BIDGBENGA NIG LTD",
    "BOMART INTEGRATED SERVICES NIG LTD",
    "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
    "DENIKE AGORO ENTERPRISES",
    "DIVINE HARCO MEDICINES",
    "EBENEZER OJO OLADAPO",
    "E'SORAE HOME STORES LIMITED(IKOTA STORE)",
    "FENCHURCH SERVICES LIMITED",
    "FOLASHADE OLAJUMOKE KALEJAIYE",
    "G&G MULTISERVICES INVESTMENT LIMITED",
    "HARRISON OGOCHUKWU EZEASOMBA",
    "HEAVENLY DEWS GLOBAL CONCEPTS LIMITED",
    "KELIZZ INTEGRATED SERVICES LIMITED",
    "LAGOON WATERS LTD",
    "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
    "MONEYTRUST MICROFINANACE BANK LTD",
    "MUSSAN OIL NIGERIA LIMITED",
    "NEWHEALTH PHARMACY LTD 3",
    "NWANERI VICTOR",
    "OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED",
    "PETER CHIDI ANUCHA",
    "PICCADILLY SUITES",
    "POWERFOIL GLOBAL SERVICES LIIMITED",
    "REIZ CONTINENTAL HOTELS LIMITED",
    "ROSEFUN VENTURES",
    "RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH",
    "SEE BY JEF LIMITED",
    "THE FILM HOUSE LIMITED",
]

searcher = MerchantSearch()
results = []

# Phase 1: Quick regular search for all
print("=" * 100)
print("PHASE 1: QUICK REGULAR SEARCH FOR ALL 33 MERCHANTS")
print("=" * 100)
for i, name in enumerate(MERCHANTS, 1):
    print(f"\n[{i:2d}/{len(MERCHANTS)}] {name}")
    try:
        r = searcher.search(name, limit=3, min_score=0)
        if r:
            for res in r[:3]:
                s = round(res.overall_score / 10, 1)
                p = res.record.get("merchant_name", "")[:60]
                print(f"      {s:4.1f}/10  {p}")
        else:
            print(f"      —  NOT FOUND")
    except Exception as e:
        print(f"      ⚠️  ERROR: {e}")

    results.append((name, r[0] if r else None))

# Phase 2: Token breakdown for not-found/low-score merchants
print("\n" + "=" * 100)
print("PHASE 2: TOKEN BREAKDOWN FOR LOW-SCORE / NOT-FOUND MERCHANTS")
print("=" * 100)

needy = [(name, r) for name, r in results if not r or r.overall_score < 20]
print(f"\n{len(needy)} merchants need deeper analysis\n")

for i, (name, _) in enumerate(needy, 1):
    print(f"[{i:2d}/{len(needy)}] {name[:55]}")
    try:
        bd = searcher.token_breakdown_search(name)
        
        # Show per-token results
        for token, matches in bd.get("token_results", {}).items():
            if matches:
                t = f"{token}: ".ljust(14)
                m = matches[0]
                s = round(m.get("score", 0) / 10, 1)
                n = m.get("name", "")[:50]
                sim = m.get("similarity", 0)
                print(f"      {t}{s:4.1f}/10  {n}  (sim={sim:.0%})")
            else:
                print(f"      {token}:  —  NO MATCH")
        
        # Show combined
        comb = bd.get("combined", [])
        if comb:
            print(f"      {'COMBINED:':14s}{'─'*60}")
            for c in comb[:3]:
                s = round(c.get("overall", 0) / 10, 1)
                n = c.get("name", "")[:50]
                tok = c.get("matched_tokens", [])
                print(f"      {'':14s}{s:4.1f}/10  {n}  tokens={tok}")
    except Exception as e:
        print(f"      ⚠️  ERROR: {e}")
    print()

print("=" * 100)
print("DONE!")
print("=" * 100)
