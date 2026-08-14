"""Focused probe: exact DB rows + raw Excel sources for each of the 6 disputed addresses."""
import sys, sqlite3, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

QUERIES = [
    ("OASIS",      ["OASIS", "MOB0LAJI", "MOBOLAJI"]),
    ("BELA VISTA", ["BELA VISTA", "LEKKI ESTATE"]),
    ("FRESHFORTE", ["FRESHFORTE"]),
    ("MARINA",     ["MARINA"]),
    ("PROVIDENCE", ["PROVIDENCE PLAZA", "OLOKONLA"]),
    ("BRITISH INT'L SCHOOL", ["BRITISH INTERNATIONAL SCHOOL"]),
]

conn = sqlite3.connect('data/intelligence.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 100)
print("DB: exact rows per address (address must contain a tight keyword)")
print("=" * 100)
for label, kws in QUERIES:
    print(f"\n### {label}")
    # match address OR merchant_name on the FIRST keyword (most specific)
    pat = kws[0]
    rows = c.execute("""
        SELECT tid, mxcode, merchant_name, address, state, static_acc_no,
               account_name, merchant_id, sheet_name, row_number
        FROM merchants
        WHERE UPPER(address) LIKE ? OR UPPER(merchant_name) LIKE ?
        ORDER BY sheet_name, row_number
    """, (f'%{pat}%', f'%{pat}%')).fetchall()
    if not rows:
        print("   (no rows)")
        continue
    seen = set()
    for r in rows:
        key = (r['tid'], r['sheet_name'], r['row_number'])
        if key in seen: continue
        seen.add(key)
        print(f"  TID {r['tid']:<10} MX {r['mxcode'] or '-':<8} | {r['sheet_name'][:44]:<44} row {r['row_number']}")
        print(f"      name : {r['merchant_name'][:60]}")
        print(f"      addr : {r['address'][:85]}")
        extra = []
        if r['state']: extra.append(f"state={r['state']}")
        if r['static_acc_no']: extra.append(f"static={r['static_acc_no']}")
        if r['account_name']: extra.append(f"acct_name={r['account_name'][:25]}")
        if r['merchant_id']: extra.append(f"mid={r['merchant_id'][:20]}")
        if extra: print(f"      {' | '.join(extra)}")

print("\n" + "=" * 100)
print("merchant_events schema + matches")
print("=" * 100)
cols = [r[1] for r in c.execute("PRAGMA table_info(merchant_events)")]
print("schema:", cols)
conn.close()
