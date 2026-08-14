"""For every TID in medplus_tids.xlsx: is its MID consistent across all DB sheets?"""
import sys, sqlite3, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd

df = pd.read_excel('data/medplus_tids.xlsx')
tids = sorted(set(str(t).strip() for t in df['TID'] if str(t).strip() and str(t).strip().lower() not in ('nan', 'none', 'not found', '-', '')))
print(f"exported matched TIDs: {len(tids)}")

conn = sqlite3.connect('data/intelligence.db')
c = conn.cursor()

clashes = []
no_mid = []
mid_map = {}
for t in tids:
    rows = c.execute("""
        SELECT merchant_id, sheet_name FROM merchants
        WHERE tid = ? AND merchant_id != '' AND merchant_id NOT IN ('0')
        ORDER BY sheet_name
    """, (t,)).fetchall()
    mids = collections.defaultdict(set)
    for mid, sheet in rows:
        mids[mid].add(sheet)
    if not mids:
        no_mid.append(t)
        continue
    if len(mids) == 1:
        mid_map[t] = next(iter(mids))
    else:
        clashes.append((t, {m: sorted(s) for m, s in mids.items()}))
        mid_map[t] = next(iter(mids))

print(f"\nTIDs with a single consistent MID: {len(tids) - len(clashes) - len(no_mid)}")
print(f"TIDs with MID clashes (different MIDs across sheets): {len(clashes)}")
print(f"TIDs with NO MID anywhere: {len(no_mid)}")

print("\n=== CLASHES ===")
for t, m in clashes:
    print(f"  {t}:")
    for mid, sheets in m.items():
        print(f"      {mid}  <- {', '.join(s.split(' :: ')[-1] for s in sheets[:3])}")

print("\n=== NO MID ===")
print("  " + ", ".join(no_mid))

print("\n=== sample consistent ===")
for t in list(mid_map.keys())[:10]:
    print(f"  {t} -> {list(mid_map[t])[0]}  ({', '.join(list(mid_map[t].values())[0][:2])})")

conn.close()
