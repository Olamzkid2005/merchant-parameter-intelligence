"""Decisive MID tiebreaker: MID embeds a state code (LA=Lagos, OY=Oyo...).
For each TID, compare each camp's MID state-code against the store's actual state
(from the address/state column of the 2ISW_Parameter sheet itself)."""
import sys, sqlite3, collections, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd

STATE_CODES = {
    'AB': 'ABIA','AD': 'ADAMAWA','AK': 'AKWA','AN': 'ANAMBRA','BA': 'BAUCHI',
    'BE': 'BAYELSA','BO': 'BORNO','BY': 'BORNO','CR': 'CROSS','DE': 'DELTA',
    'EB': 'EBONYI','ED': 'EDO','EK': 'EKITI','EN': 'ENUGU','FC': 'FCT',
    'GO': 'GOMBE','IM': 'IMO','JI': 'JIGAWA','KD': 'KADUNA','KN': 'KANO',
    'KT': 'KATSINA','KB': 'KEBBI','KG': 'KOGI','KW': 'KWARA','LA': 'LAGOS',
    'NA': 'NASARAWA','NI': 'NIGER','OG': 'OGUN','ON': 'ONDO','OS': 'OSUN',
    'OY': 'OYO','PL': 'PLATEAU','RI': 'RIVERS','SO': 'SOKOTO','TA': 'TARABA',
    'YO': 'YOBE','ZA': 'ZAMFARA',
}
STATE_NAMES = {v: k for k, v in STATE_CODES.items()}
STATE_NAMES.update({'AKWA IBOM':'AK','AKWAIBOM':'AK','CROSS RIVER':'CR','CROSSRIVER':'CR',
                    'F.C.T':'FC','ABUJA':'FC','NIGER STATE':'NI'})

df = pd.read_excel('data/medplus_tids.xlsx')
tids = sorted(set(str(t).strip() for t in df['TID']
                  if str(t).strip() and str(t).strip().lower() not in ('nan', 'none', '-', '')))

conn = sqlite3.connect('data/intelligence.db')
c = conn.cursor()

def fam(s):
    s = s.split(' :: ')[-1].strip()
    if 'NIBSS' in s: return 'NIBSS'
    if s.startswith('2ISW_Parameter'): return 'PARAM'
    if 'Change of merchant' in s: return 'CHANGE'
    if 'Sameday' in s: return 'SAMEDAY'
    return 'OTHER'

def mid_state(mid):
    m = re.search(r'(\d{3}|[A-Z]{2})(\d{3}|010)$', mid)
    if not m: return None
    code = m.group(1)
    return code if code in STATE_CODES else None

def store_state(t):
    # state column from the 2ISW_Parameter rows for this TID
    rows = c.execute("SELECT state FROM merchants WHERE tid = ? AND sheet_name LIKE '%2ISW_Parameter%' AND state != ''",
                     (t,)).fetchall()
    if not rows: return None
    return rows[0][0].strip().upper()

camp_state_hit = collections.Counter()
camp_nostate = collections.Counter()
examples = []
for t in tids:
    rows = c.execute(
        "SELECT merchant_id, sheet_name FROM merchants WHERE tid = ? AND merchant_id != '' AND merchant_id != '0'",
        (t,)).fetchall()
    camps = collections.defaultdict(set)
    for mid, sheet in rows:
        camps[fam(sheet)].add(mid)
    st = store_state(t)
    for camp, mids in camps.items():
        ok = 0
        for mid in mids:
            ms = mid_state(mid)
            if not ms:
                camp_nostate[camp] += 1
                continue
            if st and (ms == st or STATE_CODES.get(ms) == st or STATE_NAMES.get(st) == ms):
                ok += 1
        if st and ok:
            camp_state_hit[camp] += 1
        else:
            camp_state_hit[camp] += 0

print("Camp MID-state-code matches store state (TIDs where at least one MID matches):")
for camp in sorted(camp_state_hit):
    print(f"  {camp:<8}: {camp_state_hit[camp]}/{len(tids)}")

# Show a few concrete examples with states
print("\n=== concrete examples ===")
shown = 0
for t in tids:
    rows = c.execute(
        "SELECT merchant_id, sheet_name FROM merchants WHERE tid = ? AND merchant_id != '' AND merchant_id != '0'",
        (t,)).fetchall()
    camps = collections.defaultdict(set)
    for mid, sheet in rows:
        camps[fam(sheet)].add(mid)
    st = store_state(t)
    if len(camps) >= 2 and st and shown < 10:
        print(f"\n  {t}  (store state: {st})")
        for camp, mids in sorted(camps.items()):
            print(f"      {camp:<8}: {sorted(mids)}")
        shown += 1
conn.close()
