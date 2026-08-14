"""Analyze MID camps: per TID, group MIDs by which sheets carry them, and
measure intra-sheet consistency (does one sheet give the same MID everywhere?)."""
import sys, sqlite3, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd

df = pd.read_excel('data/medplus_tids.xlsx')
tids = sorted(set(str(t).strip() for t in df['TID']
                  if str(t).strip() and str(t).strip().lower() not in ('nan', 'none', '-', '')))
print(f"exported TIDs: {len(tids)}")

conn = sqlite3.connect('data/intelligence.db')
c = conn.cursor()

# per (tid, mid) -> set of sheets
tid_mid_sheets = collections.defaultdict(lambda: collections.defaultdict(set))
for t in tids:
    for mid, sheet in c.execute(
            "SELECT merchant_id, sheet_name FROM merchants WHERE tid = ? AND merchant_id != '' AND merchant_id != '0'",
            (t,)):
        tid_mid_sheets[t][mid].add(sheet)

# sheet family
def fam(s):
    s = s.split(' :: ')[-1].strip()
    if 'NIBSS' in s: return 'NIBSS FORMAT'
    if s.startswith('2ISW_Parameter'): return '2ISW_Parameter'
    if 'Change of merchant' in s: return 'Change of merchant details'
    if 'Sameday' in s: return 'Sameday_Settlement'
    if 'Deployment' in s: return 'Deployment'
    return s

# For each TID, group MIDs by the set of sheet families they appear in
camp_counts = collections.Counter()
for t in tids:
    camps = collections.defaultdict(set)
    for mid, sheets in tid_mid_sheets[t].items():
        camps[mid] = {fam(s) for s in sheets}
    # how many distinct mids
    camp_counts[len(camps)] += 1
print("distinct-MID count distribution across TIDs:")
for k in sorted(camp_counts):
    print(f"  {k} distinct MID(s): {camp_counts[k]} TIDs")

print("\n=== Example: which sheets agree on which MID (first 12 TIDs) ===")
for t in tids[:12]:
    print(f"\n  {t}:")
    for mid, sheets in sorted(tid_mid_sheets[t].items()):
        fams = sorted({fam(s) for s in sheets})
        print(f"      {mid}  <-  {', '.join(fams)}")

# Intra-sheet consistency: does 2ISW_Parameter alone ever give 2 mids for same tid?
print("\n=== TIDs where 2ISW_Parameter sheet itself gives 2+ DIFFERENT mids ===")
count_p2 = 0
for t in tids:
    mids = {mid for mid, sheets in tid_mid_sheets[t].items() if '2ISW_Parameter' in {fam(s) for s in sheets}}
    if len(mids) > 1:
        count_p2 += 1
        if count_p2 <= 6:
            print(f"  {t}: {sorted(mids)}")
print(f"  total: {count_p2}")

# How many TIDs does 2ISW_Parameter provide a MID for at all?
n_p = sum(1 for t in tids if any('2ISW_Parameter' in {fam(s) for s in sheets} for sheets in tid_mid_sheets[t].values()))
print(f"\n2ISW_Parameter provides MID for {n_p}/{len(tids)} TIDs")

# Same for NIBSS + Change + Sameday (the 'other' camp)
def in_camp(t, names):
    return any(fam(s) in names for sheets in tid_mid_sheets[t].values() for s in sheets)
n_other = sum(1 for t in tids if in_camp(t, {'Change of merchant details','Sameday_Settlement','NIBSS FORMAT'}))
print(f"Change/Sameday/NIBSS provide MID for {n_other}/{len(tids)} TIDs")

conn.close()
