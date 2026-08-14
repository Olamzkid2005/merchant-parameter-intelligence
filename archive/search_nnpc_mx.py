import sqlite3
import pandas as pd
from pathlib import Path
import re

folder = Path(r'C:\Users\David.Olamijulo\downloads\parameter') / 'data'

mx_codes = [
    'MX183916','MX183567','MX183570','MX183515','MX183520','MX183521','MX183522','MX183525',
    'MX183526','MX183531','MX183532','MX183534','MX183535','MX183536','MX183538','MX183544',
    'MX183549','MX183561','MX183565','MX183572','MX183573','MX183571','MX183562','MX183560',
    'MX183559','MX183558','MX183556','MX183591','MX183555','MX183554','MX183553','MX183552',
    'MX183548','MX183547','MX183740','MX183598','MX183639','MX183642','MX183645','MX183646',
    'MX183649','MX183650'
]

nnpc_files = [
    folder / 'NNPC PARAMETER FILE BATCH .xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 1.xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 2.xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 4.xlsx',
    folder / 'NNpc parameter master.xlsx',
]

print('=' * 120)
print('  SEARCHING NNPC FILES FOR MX CODES')
print('=' * 120)

all_results = {}  # mx_code -> details

for nf in nnpc_files:
    if not nf.exists():
        print('\n  [MISSING] ' + nf.name)
        continue
    
    try:
        xls = pd.ExcelFile(str(nf))
    except Exception as e:
        print('\n  [ERROR] ' + nf.name + ': ' + str(e))
        continue
    
    print('\n  [' + nf.name + '] (' + str(len(xls.sheet_names)) + ' sheets)')
    sheets_with_data = 0
    
    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
            df = df.dropna(axis=1, how='all')
        except:
            continue
        
        if len(df) < 2:
            continue
        
        # Find MX column
        mx_col = None
        name_col = None
        email_col = None
        slip_col = None
        acct_col = None
        phone_col = None
        contact_col = None
        
        for col in df.columns:
            cl = str(col).lower().strip()
            if cl in ['mx', 'mx code', 'mxcode', 'mx_code', 'mx_code_']:
                mx_col = col
            if any(k in cl for k in ['merchant name', 'merchant_name', 'business name', 'trading name', 'merchantname']):
                name_col = col
            if cl in ['email', 'email address', 'e-mail']:
                email_col = col
            if any(k in cl for k in ['slip header', 'slip_header']):
                slip_col = col
            if any(k in cl for k in ['account name', 'account_name']):
                acct_col = col
            if cl in ['phone', 'telephone', 'mobile', 'phone number', 'phone_no']:
                phone_col = col
            if any(k in cl for k in ['contact name', 'contact_name', 'contact person']):
                contact_col = col
        
        # If no MX column found, check for MX-like values in columns
        if mx_col is None:
            for col in df.columns:
                for idx in range(min(10, len(df))):
                    v = str(df[col].iloc[idx]).strip().upper()
                    if v.startswith('MX') and len(v) >= 7:
                        mx_col = col
                        break
                if mx_col:
                    break
        
        if mx_col is None:
            continue
        
        # Count how many of our MX codes are in this sheet
        mx_values = set()
        for idx in df.index:
            v = str(df.loc[idx, mx_col]).strip().upper()
            if v.startswith('MX') and len(v) >= 7:
                mx_values.add(v)
        
        our_mx_in_sheet = [mx for mx in mx_codes if mx.upper() in mx_values]
        if not our_mx_in_sheet:
            continue
        
        sheets_with_data += 1
        print('    Sheet: ' + sheet + ' (' + str(len(our_mx_in_sheet)) + ' matching MX codes)')
        
        # Extract details for each match
        for idx in df.index:
            v = str(df.loc[idx, mx_col]).strip().upper()
            if v not in mx_codes:
                if v not in [m.upper() for m in mx_codes]:
                    continue
            
            # Normalize v back to find in mx_codes
            match_mx = None
            for mx in mx_codes:
                if mx.upper() == v:
                    match_mx = mx
                    break
            if not match_mx:
                continue
            
            if match_mx not in all_results:
                merchant_name = str(df.loc[idx, name_col])[:70] if name_col and idx in df.index else ''
                email = str(df.loc[idx, email_col])[:50] if email_col and idx in df.index else ''
                slip = str(df.loc[idx, slip_col])[:50] if slip_col and idx in df.index else ''
                acct = str(df.loc[idx, acct_col])[:50] if acct_col and idx in df.index else ''
                phone = str(df.loc[idx, phone_col])[:30] if phone_col and idx in df.index else ''
                contact = str(df.loc[idx, contact_col])[:30] if contact_col and idx in df.index else ''
                
                all_results[match_mx] = {
                    'file': nf.name,
                    'sheet': sheet,
                    'row': idx + 2,
                    'merchant_name': merchant_name,
                    'email': email,
                    'slip_header': slip,
                    'account_name': acct,
                    'phone': phone,
                    'contact_name': contact
                }

# Print results
print('\n' + '=' * 120)
print('  RESULTS: ' + str(len(all_results)) + ' of ' + str(len(mx_codes)) + ' MX codes found')
print('=' * 120)

header = '  {:<12} {:<55} {:<35}'.format('MX Code', 'Merchant Name', 'Email')
print('\n' + header)
print('  ' + '-' * 12 + ' ' + '-' * 55 + ' ' + '-' * 35)

for mx in mx_codes:
    if mx in all_results:
        r = all_results[mx]
        name = r['merchant_name'][:55]
        email = r['email'][:35]
        print('  [FOUND] {:<12} {:<55} {:<35}'.format(mx, name, email))
    else:
        print('  [NOT]   {:<12}'.format(mx))

# Details with file/sheet/row
print('\n' + '=' * 120)
print('  DETAILS BY FILE')
print('=' * 120)

for nf in nnpc_files:
    file_results = {k: v for k, v in all_results.items() if v['file'] == nf.name}
    if file_results:
        print('\n  --- ' + nf.name + ' ---')
        fmt = '    {:<12} {:<20} {:<6} {:<55}'
        print(fmt.format('MX Code', 'Sheet', 'Row', 'Merchant Name'))
        print('    ' + '-' * 12 + ' ' + '-' * 20 + ' ' + '-' * 6 + ' ' + '-' * 55)
        for mx in sorted(file_results.keys()):
            r = file_results[mx]
            print(fmt.format(mx, r['sheet'][:20], str(r['row']), r['merchant_name'][:55]))
            if r.get('email'):
                print('    ' + ' ' * 12 + ' Email: ' + r['email'])
            if r.get('phone'):
                print('    ' + ' ' * 12 + ' Phone: ' + r['phone'])
            if r.get('contact_name'):
                print('    ' + ' ' * 12 + ' Contact: ' + r['contact_name'])

not_found = [mx for mx in mx_codes if mx not in all_results]
if not_found:
    print('\n  Still missing: ' + str(len(not_found)) + ' MX codes')
    print('  ' + ', '.join(not_found))
else:
    print('\n  ALL 42 MX CODES FOUND!')
