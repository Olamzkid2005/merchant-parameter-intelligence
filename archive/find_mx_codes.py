import sqlite3
import pandas as pd
from pathlib import Path
import re
import os

# MX codes to search
mx_codes = [
    'MX183916','MX183567','MX183570','MX183515','MX183520','MX183521','MX183522','MX183525',
    'MX183526','MX183531','MX183532','MX183534','MX183535','MX183536','MX183538','MX183544',
    'MX183549','MX183561','MX183565','MX183572','MX183573','MX183571','MX183562','MX183560',
    'MX183559','MX183558','MX183556','MX183591','MX183555','MX183554','MX183553','MX183552',
    'MX183548','MX183547','MX183740','MX183598','MX183639','MX183642','MX183645','MX183646',
    'MX183649','MX183650'
]

# === 1. CHECK merchant_search.db ===
db_path = r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

print('=' * 120)
print('  SEARCHING merchant_search.db')
print('=' * 120)

found_in_db = []
not_found = []

for mx in mx_codes:
    c.execute('''SELECT DISTINCT merchant_name, email, slip_header, account_name, phone, contact_name
                 FROM merchants WHERE mxcode = ? LIMIT 1''', (mx,))
    row = c.fetchone()
    if row and row[0]:
        found_in_db.append(mx)
        name = (row[0] or '')[:55]
        print(f'  [FOUND] {mx:<12} {name:<55}')
    else:
        not_found.append(mx)
        print(f'  [NOT]   {mx:<12}')

print(f'\nTotal: {len(mx_codes)}  Found: {len(found_in_db)}  Not found: {len(not_found)}')

# === 2. SEARCH NNPC/EXCEL FILES IN DOWNLOADS ===
downloads = Path(r'C:\Users\David.Olamijulo\downloads')

print('\n' + '=' * 120)
print('  EXCEL FILES IN DOWNLOADS FOLDER')
print('=' * 120)

# Find all Excel files
excel_files = []
for f in downloads.iterdir():
    if f.is_file() and f.suffix.lower() in ['.xlsx', '.xls', '.xlsm']:
        excel_files.append(f)

# Look specifically for NNPC files
nnpc_files = [f for f in excel_files if 'nnpc' in f.name.lower() or 'npc' in f.name.lower()]
other_excel = [f for f in excel_files if 'nnpc' not in f.name.lower() and 'npc' not in f.name.lower()]

print(f'\nTotal Excel files: {len(excel_files)}')
print(f'NNPC-related files: {len(nnpc_files)}')
print(f'Other Excel files: {len(other_excel)}')

for f in nnpc_files:
    size_mb = f.stat().st_size / (1024 * 1024)
    print(f'  [NNPC] {f.name:<60} {size_mb:.1f} MB')

print('\n--- Other Excel files ---')
for f in sorted(other_excel):
    size_mb = f.stat().st_size / (1024 * 1024)
    print(f'  {f.name:<60} {size_mb:.1f} MB')

# === 3. SEARCH NNPC FILES FOR MX CODES ===
if nnpc_files:
    print('\n' + '=' * 120)
    print('  SEARCHING NNPC FILES FOR MX CODES')
    print('=' * 120)
    mx_upper = {mx.upper() for mx in mx_codes}
    
    for nnpc_file in nnpc_files:
        try:
            xls = pd.ExcelFile(str(nnpc_file))
            file_found = False
            for sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
                df = df.dropna(axis=1, how='all')
                for col in df.columns:
                    for idx, val in df[col].items():
                        sv = str(val).strip().upper()
                        for mx in mx_codes:
                            if mx.upper() in sv:
                                if not file_found:
                                    print(f'\n  [{nnpc_file.name}]')
                                    file_found = True
                                merchant_name = ''
                                # Try to find merchant name from same row
                                name_col = None
                                for nc in df.columns:
                                    ncl = str(nc).lower().strip()
                                    if any(k in ncl for k in ['merchant name', 'merchant_name', 'business name', 'trading name', 'slip header']):
                                        name_col = nc
                                        break
                                if name_col:
                                    merchant_name = str(df.loc[idx, name_col])[:50]
                                print(f'    Sheet: {sheet:<20} Row: {idx+2:<6} Col: {str(col)[:20]:<20} MX: {mx:<12} Name: {merchant_name}')
            if not file_found:
                print(f'  {nnpc_file.name:<60} — No matching MX codes found')
        except Exception as e:
            print(f'  {nnpc_file.name:<60} — Error: {e}')

# === 4. SEARCH ALL OTHER EXCEL FILES FOR MX CODES ===
print('\n' + '=' * 120)
print('  SEARCHING ALL OTHER EXCEL FILES FOR MISSING MX CODES')
print('=' * 120)

missing_mx = [mx for mx in mx_codes if mx not in found_in_db]

if not missing_mx:
    print('\n  All MX codes already found in merchant_search.db!')
else:
    print(f'\n  Searching for {len(missing_mx)} missing MX codes in {len(other_excel)} Excel files...')
    
    # Search in the main parameter file first (for any we missed)
    main_param = downloads / '2ISW_Parameter_File 5.xlsx'
    check_files = [main_param] if main_param.exists() else []
    # Add a few likely candidates
    likely = [
        'BSP Feedback_26th July 2026.xlsx',
        'Approved_QTB_Merchant_details_V3.xlsx',
        'Terminal Registered Database ver1.xlsx',
    ]
    for lf in likely:
        p = downloads / lf
        if p.exists():
            check_files.append(p)
    
    for ef in check_files:
        try:
            xls = pd.ExcelFile(str(ef))
            file_found = False
            for sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
                df = df.dropna(axis=1, how='all')
                for col in df.columns:
                    for idx, val in df[col].items():
                        sv = str(val).strip().upper()
                        for mx in missing_mx:
                            if mx.upper() in sv:
                                if not file_found:
                                    print(f'\n  [{ef.name}]')
                                    file_found = True
                                print(f'    Sheet: {sheet:<20} Row: {idx+2:<6} Col: {str(col)[:20]:<20} MX: {mx:<12}')
            if not file_found:
                print(f'  {ef.name:<60} — No missing MX codes found')
        except Exception as e:
            print(f'  {ef.name:<60} — Error: {e}')
    
    # Also list what files we haven't checked yet
    print(f'\n  {len(missing_mx)} MX codes still missing after database + file search')
    print(f'  Missing: {", ".join(missing_mx)}')
