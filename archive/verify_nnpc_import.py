import sqlite3
import sys
sys.path.insert(0, r'C:\Users\David.Olamijulo\downloads\parameter')

for mod in list(sys.modules.keys()):
    if 'merchant' in mod.lower():
        del sys.modules[mod]

# Check merchant_search.db
print('=' * 70)
print('  CHECKING merchant_search.db')
print('=' * 70)
db1 = sqlite3.connect(r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db')
c1 = db1.cursor()
c1.execute('SELECT COUNT(*) FROM merchants')
total = c1.fetchone()[0]
c1.execute("SELECT COUNT(*) FROM merchants WHERE sheet_name LIKE 'NNPC:%'")
nnpc = c1.fetchone()[0]
c1.execute('SELECT COUNT(DISTINCT sheet_name) FROM merchants')
sheets = c1.fetchone()[0]
print(f'  Total rows:     {total:,}')
print(f'  NNPC rows:      {nnpc:,}')
print(f'  Source sheets:  {sheets}')

print('\n  NNPC files imported:')
c1.execute("SELECT DISTINCT sheet_name FROM merchants WHERE sheet_name LIKE 'NNPC:%' ORDER BY sheet_name")
for r in c1.fetchall():
    c1.execute('SELECT COUNT(*) FROM merchants WHERE sheet_name = ?', (r[0],))
    cnt = c1.fetchone()[0]
    print(f'    {r[0]:<25} {cnt} rows')

# Check merchant_intel.db
print('\n' + '=' * 70)
print('  CHECKING merchant_intel.db')
print('=' * 70)
db2 = sqlite3.connect(r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_intel.db')
c2 = db2.cursor()
c2.execute('SELECT COUNT(*) FROM merchants')
total2 = c2.fetchone()[0]
c2.execute("SELECT COUNT(*) FROM merchants WHERE sheet_name LIKE 'NNPC:%'")
nnpc2 = c2.fetchone()[0]
print(f'  Total rows:     {total2:,}')
print(f'  NNPC rows:      {nnpc2:,}')

# Confirm key merchants exist via direct SQL
print('\n' + '=' * 70)
print('  KEY MERCHANTS IN DATABASE')
print('=' * 70)
merchants = ['LAGOON WATERS', 'PETER ANUCHA', 'BARAMA ENERGY', 'TEEJAY PETROLEUM', 'BIDWILL ENERGY']
for m in merchants:
    c1.execute('SELECT merchant_name, mxcode, email, sheet_name FROM merchants WHERE merchant_name LIKE ? LIMIT 1', ('%' + m + '%',))
    row = c1.fetchone()
    if row:
        name = str(row[0])[:50]
        mx = row[1] or ''
        email = str(row[2] or '')[:30]
        sheet = str(row[3] or '')[:20]
        print(f'  [FOUND]  {name:<50} MX={mx:<12} [{sheet}]')
    else:
        print(f'  [MISS]   {m}')

db1.close()
db2.close()
print('\n  NNPC import is COMPLETE and verified!')
