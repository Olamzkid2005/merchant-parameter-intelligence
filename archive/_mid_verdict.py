"""Final verdict: for each TID, compare the PARAM-sheet MID against the
CHANGE/SAMEDAY/NIBSS camp MID, judging correctness by the store's REAL state
(derived from the address itself). Non-Lagos stores are the discriminator."""
import sys, sqlite3, collections, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd

STATE_NAMES = ['ABIA','ADAMAWA','AKWA IBOM','AKWAIBOM','ANAMBRA','BAUCHI','BAYELSA',
 'BENUE','BORNO','CROSS RIVER','CROSSRIVER','DELTA','EBONYI','EDO','EKITI','ENUGU',
 'FCT','GOMBE','IMO','JIGAWA','KADUNA','KANO','KATSINA','KEBBI','KOGI','KWARA',
 'LAGOS','NASARAWA','NIGER','OGUN','ONDO','OSUN','OYO','PLATEAU','RIVERS','SOKOTO',
 'TARABA','YOBE','ZAMFARA','ABUJA']
STATE_NAMES_U = {s for s in STATE_NAMES}

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

def real_state(t):
    """Derive the store's real state from its address (the strongest signal)."""
    addr = ''
    for r in c.execute("SELECT address FROM merchants WHERE tid = ? AND address != '' LIMIT 1", (t,)):
        addr = r[0].upper()
    if not addr: return None
    for s in STATE_NAMES_U:
        if s in addr: return s
    return None

param_correct = collections.Counter()   # camp -> #TIDs where camp MID passes state check
camp_agree = 0
camp_disagree = 0
detail = []
for t in tids:
    rows = c.execute("SELECT merchant_id, sheet_name FROM merchants WHERE tid = ? AND merchant_id != '' AND merchant_id != '0'", (t,)).fetchall()
    camps = collections.defaultdict(set)
    for mid, sheet in rows:
        camps[fam(sheet)].add(mid)
    good = set()
    for camp in ('CHANGE','SAMEDAY','NIBSS'):
        good |= camps.get(camp, set())
    good = {m for m in good if not m.startswith('Y')}
    param = camps.get('PARAM', set())
    st = real_state(t)
    if not st or not param or not good:
        continue
    # does each camp's MID embed a state code matching the real state?
    def ok_mid(mid):
        tail = mid[-5:]
        code = re.match(r'([A-Z]{2})', tail)
        return bool(code) and code.group(1) in {'LA','OY','ON','KW','FC','RI','EN','DE','AB','KN','OG','OS','AN','IM','KD','KT','PL','SO','EB','ED','EK','GO','BO','AD','AK','BA','BE','CR','JI','KB','KG','NA','NI','TA','YO','ZA','BY'} and (
            code.group(1) == st or {'LAGOS':'LA','OYO':'OY','ONDO':'ON','KWARA':'KW','FCT':'FC','ABUJA':'FC','RIVERS':'RI','ENUGU':'EN','DELTA':'DE','ABIA':'AB','KANO':'KN','OGUN':'OG','OSUN':'OS','ANAMBRA':'AN','IMO':'IM','KADUNA':'KD','KATSINA':'KT','PLATEAU':'PL','SOKOTO':'SO','EBONYI':'EB','EDO':'ED','EKITI':'EK','GOMBE':'GO','BORNO':'BO','ADAMAWA':'AD','AKWA IBOM':'AK','AKWAIBOM':'AK','BAUCHI':'BA','BAYELSA':'BE','BENUE':'BE','CROSS RIVER':'CR','CROSSRIVER':'CR','JIGAWA':'JI','KEBBI':'KB','KOGI':'KG','NASARAWA':'NA','NIGER':'NI','TARABA':'TA','YOBE':'YO','ZAMFARA':'ZA','BY':'BY'}.get(st) == code.group(1))
    p_ok = any(ok_mid(m) for m in param)
    g_ok = any(ok_mid(m) for m in good)
    if p_ok and not g_ok:
        param_correct['PARAM_only'] += 1
    elif g_ok and not p_ok:
        param_correct['GOOD_only'] += 1
    elif p_ok and g_ok:
        param_correct['both'] += 1
    else:
        param_correct['neither'] += 1
    if (p_ok and g_ok) or (not p_ok and not g_ok):
        camp_agree += 1
    else:
        camp_disagree += 1
        detail.append((t, st, sorted(param), sorted(good)))

print(f"TIDs where real state is known: {sum(param_correct.values())}")
print(f"\nWhich camp's MID matches the store's real state?")
for k, v in param_correct.most_common():
    print(f"  {k:<10}: {v}")
print(f"\ncamps AGREE: {camp_agree}   camps DISAGREE: {camp_disagree}")
print("\n=== disagreements (TID | real state | PARAM mids | GOOD mids) ===")
for t, st, p, g in detail[:25]:
    print(f"  {t} | {st:<12} | {p} | {g}")
conn.close()
