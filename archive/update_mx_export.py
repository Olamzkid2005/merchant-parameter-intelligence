import sqlite3
import pandas as pd
from pathlib import Path
import re
from collections import defaultdict

# === STEP 1: Get DB records for all MX codes ===
db_path = r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db'
conn = sqlite3.connect(db_path)

mx_codes = [
    'MX102562', 'MX124920', 'MX126819', 'MX144832', 'MX146454',
    'MX151871', 'MX154507', 'MX156674', 'MX156675', 'MX156677',
    'MX156678', 'MX156679', 'MX156681', 'MX156682', 'MX156685',
    'MX156689', 'MX156691', 'MX156692', 'MX156696', 'MX156697',
    'MX156699', 'MX156700', 'MX156702', 'MX156709', 'MX156712',
    'MX156723', 'MX156724', 'MX157720', 'MX158079', 'MX158100',
    'MX158127', 'MX159647', 'MX162723', 'MX163273', 'MX163274',
    'MX163370', 'MX163574', 'MX165197', 'MX167550', 'MX168893',
    'MX168894', 'MX183520', 'MX183526', 'MX183549', 'MX183579',
    'MX183692', 'MX184394', 'MX184402', 'MX184865',
    'MX71579', 'MX77826', 'MX89232',
]

records = []
for mx in mx_codes:
    c = conn.cursor()
    c.execute('''SELECT DISTINCT merchant_name, email, phone, contact_name, slip_header, account_name, bank, state, tid, merchant_id
                 FROM merchants WHERE LOWER(mxcode) = LOWER(?)''', (mx,))
    rows = c.fetchall()
    if rows:
        for r in rows:
            email = (r[1] or '').strip()
            records.append({
                'MX Code': mx,
                'Merchant Name': r[0] or '',
                'DB Email': email if (email and '@' in email) else '',
                'Phone': r[2] or '',
                'Contact Name': r[3] or '',
                'Slip Header': (r[4] or '')[:60],
                'Account Name': (r[5] or '')[:60],
                'Bank': r[6] or '',
                'State': r[7] or '',
                'TID': r[8] or '',
                'MID': r[9] or '',
                'Status': 'Found'
            })
    else:
        records.append({
            'MX Code': mx,
            'Merchant Name': '-- NOT FOUND --',
            'DB Email': '',
            'Phone': '',
            'Contact Name': '',
            'Slip Header': '',
            'Account Name': '',
            'Bank': '',
            'State': '',
            'TID': '',
            'MID': '',
            'Status': 'Not Found in Database'
        })

conn.close()

# === STEP 2: Get real emails from the raw Excel workbook ===
print('Searching workbook for real emails...')
xls_path = Path(r'C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx')
xls = pd.ExcelFile(str(xls_path))

email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Map: merchant_name -> set of emails from workbook
wb_emails = defaultdict(set)

for sheet in xls.sheet_names:
    if 'Deactivated' in sheet or 'State Code' in sheet:
        continue
    df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
    df = df.dropna(axis=1, how='all')

    # Find name column
    name_col = None
    for col in df.columns:
        cl = str(col).lower().strip()
        if any(k in cl for k in ['merchant name', 'merchant_name', 'business name', 'trading name', 'slip header']):
            name_col = col
            break

    if name_col is None:
        continue

    for col in df.columns:
        for idx, val in df[col].items():
            sv = str(val).strip()
            matches = email_pattern.findall(sv)
            if matches:
                merchant = str(df.at[idx, name_col]).strip() if pd.notna(df.at[idx, name_col]) else ''
                for email in matches:
                    if not email.lower().endswith(('.png', '.jpg', '.gif', '.pdf', '.doc', '.jpeg')):
                        wb_emails[merchant.upper()].add(email.lower())

# === STEP 3: Merge workbook emails into records ===
for rec in records:
    m_name = rec['Merchant Name'].upper().strip()

    # Try exact match first
    if m_name in wb_emails:
        existing = set(e for e in wb_emails[m_name])
        if rec['DB Email'] and rec['DB Email'] not in existing:
            existing.add(rec['DB Email'])
        rec['Workbook Email(s)'] = ', '.join(sorted(existing))
        continue

    # Try partial match
    matched_emails = set()
    m_parts = set(m_name.split())
    for wb_name, wb_es in wb_emails.items():
        wb_parts = set(wb_name.split())
        common = m_parts & wb_parts
        if len(common) >= 2 or (len(common) >= 1 and any(len(p) > 5 for p in common)):
            matched_emails.update(wb_es)

    if matched_emails:
        if rec['DB Email']:
            matched_emails.add(rec['DB Email'])
        rec['Workbook Email(s)'] = ', '.join(sorted(matched_emails))
    elif rec['DB Email']:
        rec['Workbook Email(s)'] = rec['DB Email']
    else:
        rec['Workbook Email(s)'] = ''

# === STEP 4: Write to Excel ===
df = pd.DataFrame(records)

# Reorder columns: put emails first
cols = ['MX Code', 'Merchant Name', 'Workbook Email(s)', 'DB Email', 'Phone',
        'Contact Name', 'Slip Header', 'Account Name', 'Bank', 'State', 'TID', 'MID', 'Status']
df = df[cols]

output_path = Path(r'C:\Users\David.Olamijulo\downloads\parameter\reports\MX_Codes_Export.xlsx')
df.to_excel(str(output_path), index=False, sheet_name='MX Codes')

print(f'Saved to: {output_path}')
print(f'Total rows: {len(df)}')

# Stats
has_email = df[df['Workbook Email(s)'] != '']
no_email = df[df['Workbook Email(s)'] == '']
print(f'Merchants with real emails: {len(has_email)}')
print(f'Merchants without emails:   {len(no_email)}')
print()
print('--- MERCHANTS WITH REAL EMAILS ---')
for _, row in has_email.iterrows():
    mx = row['MX Code']
    name = row['Merchant Name'][:45]
    email = row['Workbook Email(s)'][:55]
    print(f'  {mx:<12} {name:<45} {email}')
