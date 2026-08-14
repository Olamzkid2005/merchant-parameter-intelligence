import sys
sys.path.insert(0, r'C:\Users\David.Olamijulo\downloads\parameter')

import pandas as pd
import sqlite3

DB_PATH = r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db'
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

mx_codes = [
    'MX183916','MX183567','MX183570','MX183515','MX183520','MX183521','MX183522','MX183525',
    'MX183526','MX183531','MX183532','MX183534','MX183535','MX183536','MX183538','MX183544',
    'MX183549','MX183561','MX183565','MX183572','MX183573','MX183571','MX183562','MX183560',
    'MX183559','MX183558','MX183556','MX183591','MX183555','MX183554','MX183553','MX183552',
    'MX183548','MX183547','MX183740','MX183598','MX183639','MX183642','MX183645','MX183646',
    'MX183649','MX183650'
]

print('=' * 120)
print('  EXPORTING NNPC MX CODES TO EXCEL')
print('=' * 120)

records = []
for mx in mx_codes:
    c.execute('''SELECT DISTINCT merchant_name, mxcode, email, phone, contact_name, 
                        slip_header, account_name, tid, sheet_name, address
                 FROM merchants 
                 WHERE mxcode = ? 
                 ORDER BY sheet_name
                 LIMIT 1''', (mx,))
    row = c.fetchone()
    if row:
        name = (row[0] or '')[:70]
        mx_val = row[1] or mx
        email_val = row[2] or ''
        phone_val = row[3] or ''
        contact_val = row[4] or ''
        slip_val = row[5] or ''
        acct_val = row[6] or ''
        tid_val = row[7] or ''
        sheet_val = row[8] or ''
        addr_val = row[9] or ''
        records.append([mx_val, name, email_val, phone_val, contact_val, slip_val, acct_val, tid_val, sheet_val, addr_val])
        print(f'  [OK]   {mx:<12} {name[:50]:<55}')
    else:
        records.append([mx, '(Not found in DB)', '', '', '', '', '', '', '', ''])
        print(f'  [MISS] {mx:<12}')

# Create DataFrame
df = pd.DataFrame(records, columns=[
    'MX Code', 'Merchant Name', 'Email', 'Phone', 'Contact Name',
    'Slip Header', 'Account Name', 'TID', 'Source Sheet', 'Address'
])

output_path = r'C:\Users\David.Olamijulo\downloads\parameter\reports\NNPC_MX_Codes.xlsx'
with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='NNPC MX Codes', index=False)
    ws = writer.sheets['NNPC MX Codes']
    for col_idx, col in enumerate(df.columns):
        max_len = len(str(col))
        for row_idx in range(len(df)):
            cell_val = str(df.iloc[row_idx, col_idx] or '')
            max_len = max(max_len, len(cell_val))
        ws.column_dimensions[chr(65 + col_idx) if col_idx < 26 else 'A'].width = min(max_len + 2, 60)

print()
print('=' * 60)
print(f'  FILE SAVED: {output_path}')
print(f'  Rows: {len(df)}')
print(f'  Columns: {len(df.columns)}')
print(f'  Status: {"ALL 42 FOUND" if all(r[1] != "(Not found in DB)" for r in records) else "Some missing"}')
print('=' * 60)
