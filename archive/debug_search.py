"""
Debug: trace why "THE FILM HOUSE" returns "THE GEORGE HOTEL" at score 100.
"""
import sys
sys.path.insert(0, r"C:\Users\David.Olamijulo\downloads\parameter")
from merchant_intelligence.config import FIELD_WEIGHTS, GENERIC_WORDS
from merchant_intelligence.matcher import MerchantMatcher
from merchant_intelligence.database import DatabaseManager
from merchant_intelligence.aliases import AliasEngine

db = DatabaseManager()
conn = db.connect()
cur = conn.cursor()

# 1. Does THE FILM HOUSE LIMITED exist in the database?
print("=" * 70)
print("1. Does 'THE FILM HOUSE LIMITED' exist in the DB?")
cur.execute("SELECT COUNT(*) FROM merchants WHERE merchant_name LIKE '%FILM HOUSE%'")
count = cur.fetchone()[0]
print(f"   Merchants with '%FILM HOUSE%': {count}")

cur.execute("SELECT id, merchant_name, tid, mxcode, sheet_name FROM merchants WHERE merchant_name LIKE '%FILM HOUSE%' LIMIT 20")
rows = cur.fetchall()
for r in rows:
    print(f"   ID={r['id']}: {r['merchant_name'][:60]} | TID={r['tid']} | MX={r['mxcode']}")

# 2. What does FTS5 return for 'THE FILM HOUSE'?
print("\n" + "=" * 70)
print("2. FTS5 results for THE FILM HOUSE (top 20):")
safe = db._sanitise_fts_query("THE FILM HOUSE")
print(f"   Sanitised FTS query: '{safe}'")
cur.execute("""
    SELECT m.id, m.merchant_name, m.slip_header, m.email, m.address, rank
    FROM merchants_fts
    JOIN merchants m ON m.id = merchants_fts.rowid
    WHERE merchants_fts MATCH ?
    ORDER BY rank
    LIMIT 20
""", (safe,))
fts_rows = cur.fetchall()
for r in fts_rows:
    name = r['merchant_name'] or ''
    addr = (r['address'] or '')[:40]
    print(f"   rank={r['rank']:.3f}  name={name[:50]}  addr={addr}")

# 3. What does column search return for token 'THE'?
print("\n" + "=" * 70)
print("3. Column search for 'THE' (merchant_name, limit 10):")
cur.execute("SELECT id, merchant_name FROM merchants WHERE merchant_name LIKE '%THE%' LIMIT 10")
col_rows = cur.fetchall()
for r in col_rows:
    print(f"   ID={r['id']}: {r['merchant_name'][:60]}")

# 4. What are the actual field values for THE GEORGE HOTEL?
print("\n" + "=" * 70)
print("4. Field values for THE GEORGE HOTEL (first few rows):")
cur.execute("""
    SELECT id, merchant_name, slip_header, email, phone, 
           address, account_name, remarks, alias
    FROM merchants WHERE merchant_name LIKE '%GEORGE HOTEL%'
    LIMIT 5
""")
hotel_rows = cur.fetchall()
for r in hotel_rows:
    print(f"\n   ID={r['id']}: {r['merchant_name']}")
    for key in ['slip_header', 'email', 'phone', 'address', 'account_name', 'remarks', 'alias']:
        val = r[key] if r[key] else '(empty)'
        print(f"      {key}: {str(val)[:80]}")

# 5. Check if 'THE FILM HOUSE' (or part of it) appears in any field of THE GEORGE HOTEL
print("\n" + "=" * 70)
print("5. Does raw query 'THE FILM HOUSE' appear in any GEORGE HOTEL field?")
q = "THE FILM HOUSE"
for r in hotel_rows:
    for key in ['slip_header', 'email', 'phone', 'address', 'account_name', 'remarks', 'alias']:
        val = str(r[key] or '')
        if q.upper() in val.upper():
            print(f"   *** FOUND! '{q}' in {key} of ID={r['id']}")
            print(f"       Value: {val[:100]}")

# 6. Check tokenise output
print("\n" + "=" * 70)
print("6. Token analysis:")
from merchant_intelligence.matcher import MerchantMatcher
print(f"   _tokenise('THE FILM HOUSE') = {MerchantMatcher._tokenise('THE FILM HOUSE')}")
print(f"   _tokenise('THE GEORGE HOTEL') = {MerchantMatcher._tokenise('THE GEORGE HOTEL')}")
print(f"   'THE' in GENERIC_WORDS? {'THE' in [w.upper() for w in GENERIC_WORDS]}")

conn.close()
