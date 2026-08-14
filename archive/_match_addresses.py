"""Match pasted addresses to TIDs — Medplus.xlsx first, DB as fallback."""
import sys, re, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'scripts'); sys.path.insert(0, '.')

import pandas as pd
from merchant_intelligence.fuzzy import token_sort_ratio, fuzzy_ratio

ADDRESSES = [
    "MEDPLUS STUDIO 24, PLOT 100, 3RD AVENUE GWARINPA, ABUJA",
    "MEDPLUS BLENCO SUPERMARKET LEKKI EPE EXPRESSWAY BY EPUTU BUS STOP IBEJU LEKKI, LAGOS STATE",
    "MEDPLUS 31A ISAAC JOHN STREET VICTORIA ISLAND LAGOS",
    "MEDPLUS ADENIRAN OGUNSANYA SHOPING MALL, SURULERE LAGOS",
    "MEDPLUS SHOP 33A, ASABA MALL, OKPANAM, ROAD, ASABA, DELTA.",
    "MEDPLUS SHOP 65/66 IBATORO ROAD, AKURE.",
    "MEDPLUS 78, NANA PLAZA AMINU-KANO CRESCENT WUSE II ABUJA.",
    "ENUGU RETAIL CENTRE LTD, OFF PRESIDENTIAL ROAD OPP MICHEAL OKPARA ENUGU",
    "MEDPLUS PLOT 2 ADMIRALTY WAY LEKKI PHASE 1 LAGOS",
    "CHEVRON DRIVE CHEVRON",
    "MEDPLUS THE PALMS, 1 BIS WAY, LEKKI, LAGOS.",
    "MEDPLUS SHOP B4 & B5, CAPITAL GARDEN MALL, ORCHID ROAD, ETI-OSA LOCAL GOVT LAGOS",
    "MEDPLUS 15 ODUDUWA WAY, IKEJA LAGOS",
    "MEDPLUS ABS AWKA ROAD BY 7 PARK ROAD ONITSHA ANAMBRA STATE",
    "MEDPLUS NOVARE MALL, CNR OF LEKKI EXP/WAY AND MONASTERY RD, SANGOTEDO, LEKKI, LAGOS.",
    "CABANA AREA, PRESIDENTIAL HOTEL, NO 1 BIRABI STREET, PORT HARCOURT - ABA EXPRESS, PORT HARCOURT, RIVERS",
    "MEDPLUS ENUGU RETAIL CENTER LIMITED, NKPOKITI ROAD, OFF PRESIDENTIAL ROAD, OPP OKPARA SQUARE, ENUGU.",
    "MEDPLUS 22 ROAD TASTY FRIED CHICKEN BUILDING FESTAC",
    "MEDPLUS WORLD BANK OWERRI IMO STATE",
    "MEDPLUS SHOP 23 CIRCLE MALL JAKANDE ROUNDABOUT LEKKI EPE EXPRESSWAY LEKKI, LAGOS STATE",
    "MEDPLUS 38 SALAMI SUAIBU STREET PADRO ROAD, PALMGROOVE, LAGOS NIGERIA",
    "SIMBIAT IKEJA MALL, 22, SIMBIAT ABIOLA WAY, IKEJA LAGOS",
    "MEDPLUS 47, CALABAR ROAD, CALABAR.",
    "MEDPLUS BERA ESTATE 20B UBA ROAD, CHEVRON DRIVE, LEKKI LAGOS",
    "MEDPLUS 107, ALLEN AVENUE, IKEJA LAGOS",
    "MEDPLUS 193, BRITISH AMERICAN JUNCTION, JOS, PLATEAU STATE",
    "58, IGBOGBO IKORODU ROAD, OPPOSITE ORIWU MODEL COLLEGE, IKORODU LAGOS",
    "MEDPLUS GREENVILLE PLAZA, 19 CMD ROAD IKOSI-KETU, LAGOS.",
    "57 ADEKUNLE BANJO STREET OFF CMD ROAD MAGODO",
    "261, HERBERT MACAULEY WAY YABA",
    "MEDPLUS PRICELESS MALL, PLOT C9 OKIGWE ROAD BY ORJI FLYOVER, OWERRI IMO STATE",
    "MEDPLUS CHEVRON DRIVE CHEVRON",
    "MEDPLUS MALL 55, KUMASI CRESCENT, WUSE 2, ABUJA",
    "1 AZIKIWE ROAD PORTHARCOURT",
    "MEDPLUS OASIS CENTER, MOB0LAJI BANK ANTHONY WAY OPPOSITE THE POLICE COLLEGE, IKEJA",
    "MEDPLUS QMB MART BLOCK, 138 PLOT 8 LEKKI SCHEME 1, LAGOS",
    "MEDPLUS 45 SAKA TINUBU STREET VICTORIA ISLAND LAGOS",
    "MEDPLUS GREENVILLE MALL, NO 10, ADEOLA ODEKU STREET, VICTORIA ISLAND",
    "10, LALUBU STREET, OKE ILEWO ABEOKUTA, OGUN",
    "MEDPLUS NEW INTERNATIONAL TERMINAL OF MMIA, IKEJA, LAGOS.",
    "39 LEKKI ESTATE ROAD WITHIN BELA VISTA ESTATE FREEDOM WAY LEKKI LAGOS",
    "MEDPLUS LACIUDAD MALL BLOCK XXVI PLOT 1 LEKKI EPE EXPRESSWAY IKOTA LAGOS",
]

# ── Normalize for comparison ────────────────────────────────────────────
STOP = set("""THE A AN AND OR FOR TO IN ON AT BY WITH OF IS IT AS BE THIS THAT FROM
              SHOP SHOPS MEDPLUS MEDPLUS""".split())
def norm(s):
    s = re.sub(r"[^A-Za-z0-9 ]", " ", str(s).upper())
    toks = [t for t in s.split() if t not in STOP and len(t) > 1]
    return toks

def jaccard(a, b):
    if not a or not b: return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)

# ── Corpus 1: Medplus.xlsx (store name + address + tid + static acct) ───
med_rows = []
raw = pd.read_excel('data/Medplus.xlsx', dtype=str, keep_default_na=False, header=0)
for _, r in raw.iterrows():
    addr = (str(r.get('ADDRESS', '')) + ' ' + str(r.get('STORE NAME', ''))).strip()
    med_rows.append({
        'store': str(r.get('STORE NAME', '')).strip(),
        'addr': addr,
        'tid': str(r.get('TERMINAL ID', '')).strip(),
        'static': str(r.get('STATIC ACCOUNT', '')).strip(),
    })

# ── Corpus 2: current DB (address column) ───────────────────────────────
conn = sqlite3.connect('data/intelligence.db')
db_rows = []
for r in conn.execute("SELECT merchant_name, address, tid, mxcode, sheet_name FROM merchants WHERE address != '' AND tid != ''"):
    db_rows.append({'store': r[0] or '', 'addr': r[1] or '', 'tid': r[2] or '',
                    'mx': r[3] or '', 'sheet': r[4] or ''})
conn.close()

def best_match(tokens, corpus, keys=('addr',)):
    best, best_s = None, 0.0
    for row in corpus:
        for k in keys:
            rtoks = norm(row[k])
            s = 0.65 * jaccard(tokens, rtoks) + 0.35 * token_sort_ratio(
                ' '.join(tokens), ' '.join(rtoks))
            if s > best_s:
                best_s, best = s, row
    return best, best_s

print(f"{'#':>2}  {'SCORE':>5}  TID        STORE / MATCH")
print("-" * 100)
found = 0
for i, addr in enumerate(ADDRESSES, 1):
    toks = norm(addr)
    m, s = best_match(toks, med_rows)
    source = 'medplus.xlsx'
    if not m or s < 0.42:
        m2, s2 = best_match(toks, db_rows)
        if m2 and s2 > s:
            m, s, source = m2, s2, 'db'
    if m and s >= 0.42:
        found += 1
        print(f"{i:>2}  {s:5.2f}  {m['tid']:<10} {m['store'][:40]:<40} [{source}]  :: {addr[:50]}")
    else:
        print(f"{i:>2}  {'?':>5}  {'-':<10} NOT FOUND {'':<29} :: {addr[:50]}")
print("-" * 100)
print(f"matched: {found}/{len(ADDRESSES)}")
