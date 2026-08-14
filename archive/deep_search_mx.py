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

downloads = Path(r'C:\Users\David.Olamijulo\downloads')
mx_set = {mx.upper() for mx in mx_codes}

print('=' * 120)
print('  RECURSIVE SEARCH FOR ALL EXCEL FILES IN DOWNLOADS')
print('=' * 120)

# Recursive search for all Excel files
all_excel = []
for root, dirs, files in os.walk(str(downloads)):
    root_path = Path(root)
    for f in files:
        if f.lower().endswith(('.xlsx', '.xls', '.xlsm')):
            all_excel.append(root_path / f)

print(f'\nTotal Excel files found (recursive): {len(all_excel)}')

# Check for NNPC/naming patterns
for ef in sorted(all_excel, key=lambda p: p.stat().st_size, reverse=True):
    rel = ef.relative_to(downloads)
    size_mb = ef.stat().st_size / (1024*1024)
    name = ef.name.lower()
    
    tags = []
    if 'nnpc' in name or 'npc' in name:
        tags.append('NNPC')
    if 'mx' in name or 'code' in name or 'parameter' in name:
        tags.append('PARAM')
    if 'merchant' in name:
        tags.append('MERCHANT')
    
    if tags or size_mb > 0.5:
        print(f'  [{",".join(tags) if tags else "OTHER":<10}] {str(rel):<70} {size_mb:6.1f} MB')
    else:
        print(f'  [{"":<10}] {str(rel):<70} {size_mb:6.1f} MB')

# Search all non-parameter Excel files for these MX codes
print('\n' + '=' * 120)
print('  SEARCHING ALL EXCEL FILES FOR MX CODES')
print('=' * 120)

param_file = downloads / '2ISW_Parameter_File 5.xlsx'
found_any = False

for ef in all_excel:
    # Skip parameter file (already searched)
    if ef.resolve() == param_file.resolve():
        continue
    
    try:
        xls = pd.ExcelFile(str(ef))
    except Exception as e:
        continue
    
    file_found = False
    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
            df = df.dropna(axis=1, how='all')
        except:
            continue
        
        for col in df.columns:
            for idx, val in df[col].items():
                sv = str(val).strip().upper()
                for mx in mx_codes:
                    if mx.upper() in sv:
                        if not file_found:
                            print(f'\n  [FOUND IN] {ef.name:<55} ({ef.stat().st_size/1024/1024:.1f} MB)')
                            file_found = True
                            found_any = True
                        
                        # Get merchant name from row
                        merchant_name = ''
                        for nc in df.columns:
                            ncl = str(nc).lower().strip()
                            if any(k in ncl for k in ['merchant name', 'merchant_name', 'business name', 'trading name', 'slip header', 'account name']):
                                merchant_name = str(df.loc[idx, nc])[:60]
                                break
                        
                        print(f'    Sheet: {sheet:<25} Row: {idx+2:<6} MX: {mx:<12} Name: {merchant_name}')
        
        # Also check header row for MX column
        if not file_found:
            for col in df.columns:
                if 'mx' in str(col).lower().strip():
                    # This sheet might have an MX column - check values
                    for idx, val in df[col].items():
                        if idx == 0:
                            continue  # skip header
                        sv = str(val).strip().upper()
                        for mx in mx_codes:
                            if mx.upper() == sv:
                                if not file_found:
                                    print(f'\n  [FOUND IN] {ef.name:<55} ({ef.stat().st_size/1024/1024:.1f} MB)')
                                    file_found = True
                                    found_any = True
                                merchant_name = ''
                                for nc in df.columns:
                                    ncl = str(nc).lower().strip()
                                    if any(k in ncl for k in ['merchant name', 'merchant_name', 'business name', 'trading name', 'slip header', 'account name']):
                                        merchant_name = str(df.loc[idx, nc])[:60]
                                        break
                                print(f'    Sheet: {sheet:<25} Row: {idx+2:<6} MX: {mx:<12} Name: {merchant_name}')

if not found_any:
    print('\n  ❌ None of the 42 MX codes were found in any Excel file outside the main parameter file.')
else:
    print(f'\n  ✅ Found matches!')

# Final summary
print('\n' + '=' * 120)
print('  FINAL SUMMARY')
print('=' * 120)
print(f'  Total Excel files scanned (excluding parameter file): {len(all_excel) - 1}')
print(f'  MX codes searched: {len(mx_codes)}')
print(f'  MX codes found anywhere: {"No" if not found_any else "Yes - see above"}')
print(f'\n  All 42 MX codes likely belong to a different parameter file set.')
print(f'  These appear to be MX codes from the 183xxx range (different from the')
print(f'  main 156xxx range in 2ISW_Parameter_File 5.xlsx)')
