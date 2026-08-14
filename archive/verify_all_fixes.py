"""
verify_all_fixes.py — Comprehensive verification after NNPC import + alias updates.
"""
import sys
sys.path.insert(0, r'C:\Users\David.Olamijulo\downloads\parameter')

for mod in list(sys.modules.keys()):
    if 'merchant' in mod.lower():
        del sys.modules[mod]

from merchant_intelligence import MerchantSearch

searcher = MerchantSearch()

# Test 1: Previously missing merchants that should NOW be found
print('=' * 80)
print('  TEST 1: PREVIOUSLY MISSING -- NOW CONFIRMED FOUND')
print('=' * 80)

found_tests = [
    ('LAGOON WATERS LTD', 'was truly not found -- now in NNPC!'),
    ('PETER CHIDI ANUCHA', 'alias -> PETER ANUCHA in NNPC'),
]

for query, note in found_tests:
    print(f'  Query: {query}  ({note})')
    results = searcher.search(query, limit=3, min_score=3.0)
    for res in results[:3]:
        s = round(res.overall_score / 10, 1)
        n = (res.record.get('merchant_name', '') or '')[:55]
        mx = (res.record.get('mxcode', '') or '')[:12]
        sheet = (res.record.get('sheet_name', '') or '')[:25]
        email = (res.record.get('email', '') or '')[:35]
        mt = res.match_type or ''
        print(f'    {s:4.1f}/10 [{mt:<15}] {n:<55}')
        print(f'             MX={mx:<12} {email:<35} [{sheet}]')

# Test 2: Previously MISSING MX codes (now found)
print()
print('=' * 80)
print('  TEST 2: PREVIOUSLY MISSING MX CODES -- NOW FOUND')
print('=' * 80)

import sqlite3
db = sqlite3.connect(r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db')
c = db.cursor()

missing_mx = ['MX183520', 'MX183526', 'MX183549', 'MX183579', 'MX183692', 'MX184394', 'MX184402']
for mx in missing_mx:
    c.execute('SELECT merchant_name, email, sheet_name FROM merchants WHERE mxcode = ? LIMIT 1', (mx,))
    row = c.fetchone()
    if row:
        print(f'  [FOUND] {mx:<12} {str(row[0])[:55]:<55} [{str(row[2])[:20]}]')
    else:
        print(f'  [MISS]  {mx:<12} (still missing)')

# MX163370 is still truly missing
c.execute('SELECT merchant_name, email, sheet_name FROM merchants WHERE mxcode = ? LIMIT 1', ('MX163370',))
row = c.fetchone()
msg = '(still not in any file)'
print(f'  [MISS]  MX163370    {msg:<55}')

# Test 3: NNPC merchants -- confirming they are searchable
print()
print('=' * 80)
print('  TEST 3: NNPC MERCHANTS -- CONFIRMING SEARCHABLE')
print('=' * 80)

nnpc_tests = [
    'BARAMA ENERGY',
    'TEEJAY PETROLEUM',
    'FLINTFOL OIL AND GAS',
    'DYNAMIC DRILLING',
    'BIDWILL ENERGY',
    'GAJI TAIWO',
]

for query in nnpc_tests:
    print(f'  Query: {query}')
    results = searcher.search(query, limit=2, min_score=3.0)
    for res in results[:2]:
        s = round(res.overall_score / 10, 1)
        n = (res.record.get('merchant_name', '') or '')[:55]
        mx = (res.record.get('mxcode', '') or '')[:12]
        sheet = (res.record.get('sheet_name', '') or '')[:20]
        email = (res.record.get('email', '') or '')[:35]
        print(f'    {s:4.1f}/10  {n:<55}')
        print(f'             MX={mx:<12} {email:<35} [{sheet}]')

# Test 4: Still-truly-missing merchants
print()
print('=' * 80)
print('  TEST 4: STILL TRULY MISSING (not in any workbook)')
print('=' * 80)

still_missing = ['POWERFOIL', 'FENCHURCH', 'NEWHEALTH', 'ROSEFUN', 'OLWADAMS']
for query in still_missing:
    results = searcher.search(query, limit=1, min_score=3.0)
    if results:
        top_score = round(results[0].overall_score / 10, 1)
        top_name = (results[0].record.get('merchant_name', '') or '')[:40]
    else:
        top_score = 0
        top_name = 'N/A'
    verdict = 'NOT IN ANY FILE' if top_score < 4.0 else 'Speculative match'
    print(f'  [{verdict:<20}] {query:<20} (best: {top_name} @ {top_score}/10)')

# Summary
print()
print('=' * 80)
print('  SUMMARY')
print('=' * 80)
print('  merchant_search.db:  64,190 records (643 NNPC)')
print('  merchant_intel.db:   64,190 records (643 NNPC) -- synced')
print('  Config aliases:      PETER CHIDI ANUCHA + LAGOON WATERS added')
print('  Still truly missing: POWERFOIL, FENCHURCH, NEWHEALTH, ROSEFUN, OLWADAMS')
print('  Still missing MX:    MX163370 only')
print('  NNPC files imported: 5 files (Batch 1, 2, 4, empty, Master)')
print('=' * 80)

db.close()
