"""
cross_check.py -- Cross-check database row count vs Excel file.
"""

import json
import sqlite3
from pathlib import Path

import pandas as pd

EXCEL_PATH = Path(r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx")
DB_PATH = Path(r"C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db")

# --- 1. Count Excel rows per sheet ---

print("=" * 70)
print("  SHEET-BY-SHEET ROW COMPARISON")
print("=" * 70)

xls = pd.ExcelFile(str(EXCEL_PATH))
excel_counts = {}

for sheet in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=sheet)
    df_clean = df.dropna(axis=1, how="all")
    excel_counts[sheet] = len(df_clean)

total_excel = sum(excel_counts.values())

# --- 2. Count DB rows per sheet ---

conn = sqlite3.connect(str(DB_PATH))
c = conn.cursor()

c.execute("SELECT sheet_name, COUNT(*) FROM merchants GROUP BY sheet_name")
db_counts = dict(c.fetchall())
total_db = sum(db_counts.values())

# --- 3. Compare ---

HDR = "  {:<35s}  {:>8s}  {:>8s}  {:>8s}"
ROW = "  {:<35s}  {:>8,}  {:>8,}  {:>+7,d}"
SEP = "  " + "-" * 35 + "  " + "-" * 8 + "  " + "-" * 8 + "  " + "-" * 8

print()
print(HDR.format("Sheet", "Excel", "DB", "Diff"))
print(SEP)

for sheet in sorted(excel_counts.keys(), key=lambda s: -excel_counts[s]):
    ex = excel_counts.get(sheet, 0)
    db = db_counts.get(sheet, 0)
    diff = ex - db
    marker = "  <-- MISSING!" if diff > 0 else ""
    print(ROW.format(sheet, ex, db, diff) + marker)

print(SEP)
print(ROW.format("TOTAL", total_excel, total_db, total_excel - total_db))

# --- 4. Empty/numeric merchant_name analysis ---

print()
print("=" * 70)
print("  EMPTY / CODE merchant_name BY SHEET")
print("=" * 70)

c.execute("""
    SELECT sheet_name,
           COUNT(*) as total,
           SUM(CASE WHEN merchant_name IS NULL OR merchant_name = '' THEN 1 ELSE 0 END) as empty,
           SUM(CASE WHEN merchant_name GLOB '*[0-9]*' AND merchant_name NOT GLOB '*[A-Za-z]*' THEN 1 ELSE 0 END) as numeric
    FROM merchants
    GROUP BY sheet_name
    HAVING empty > 0 OR numeric > 0
    ORDER BY empty DESC, numeric DESC
""")

HDR2 = "  {:<35s}  {:>7s}  {:>7s}  {:>8s}"
ROW2 = "  {:<35s}  {:>7,}  {:>7,}  {:>8,}"
SEP2 = "  " + "-" * 35 + "  " + "-" * 7 + "  " + "-" * 7 + "  " + "-" * 8

print()
print(HDR2.format("Sheet", "Total", "Empty", "Numeric"))
print(SEP2)

total_empty = 0
total_numeric = 0
for row in c.fetchall():
    sheet, total, empty, numeric = row
    total_empty += empty
    total_numeric += numeric
    print(ROW2.format(sheet, total, empty, numeric))

print(SEP2)
print(ROW2.format("TOTAL", total_db, total_empty, total_numeric))

# --- 5. NIBSS FORMAT: investigate ---

print()
print("=" * 70)
print("  NIBSS FORMAT: merchant_name='ISW' investigation")
print("=" * 70)

c.execute("""
    SELECT slip_header, account_name, contact_name, email, tid
    FROM merchants
    WHERE sheet_name = '2ISW NIBSS FORMAT'
      AND merchant_name = 'ISW'
    LIMIT 3
""")
print(f"\n  Sample rows with merchant_name='ISW':")
for r in c.fetchall():
    print(f"    slip={r[0]}  acct={r[1]}  contact={r[2]}  email={r[3]}  tid={r[4]}")

# Check raw_data for merchant-name-like columns
print(f"\n  Raw data columns (merchant-related) for one 'ISW' row:")
c.execute("""
    SELECT raw_data FROM merchants
    WHERE sheet_name = '2ISW NIBSS FORMAT'
      AND merchant_name = 'ISW'
    LIMIT 1
""")
raw = c.fetchone()[0]
try:
    data = json.loads(raw)
    for k, v in sorted(data.items()):
        if 'merchant' in k.lower() or 'name' in k.lower() or 'acquirer' in k.lower():
            print(f"    {k}: \"{v}\"")
except:
    print(f"    (parse error)")

# --- 6. SWEB_MARYLAND MALL raw_data ---

print()
print("=" * 70)
print("  SWEB_MARYLAND MALL - Raw Excel Columns")
print("=" * 70)

c.execute("""
    SELECT raw_data, merchant_name, slip_header, account_name
    FROM merchants
    WHERE slip_header LIKE '%MARYLAND%'
    LIMIT 1
""")
row = c.fetchone()
if row:
    print(f"\n  merchant_name (in DB): \"{row[1]}\"")
    print(f"  slip_header  (in DB): \"{row[2]}\"")
    print(f"  account_name (in DB): \"{row[3]}\"")
    print()
    try:
        data = json.loads(row[0])
        print(f"  ALL columns from Excel (raw_data):")
        for k, v in sorted(data.items()):
            print(f"    {k}: \"{v}\"")
    except Exception as e:
        print(f"  Error parsing raw_data: {e}")

conn.close()

print()
print("=" * 70)
print("  Done.")
print("=" * 70)
