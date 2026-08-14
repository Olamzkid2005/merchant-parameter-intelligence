import sqlite3

db = sqlite3.connect(r'C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db')
c = db.cursor()

# Get ALL NNPC records at once (efficient single query)
print('Loading NNPC records...')
c.execute("SELECT merchant_name, mxcode, email, slip_header, account_name, contact_name, phone, sheet_name FROM merchants WHERE sheet_name LIKE 'NNPC:%'")
all_nnpc = c.fetchall()
print(f'Total NNPC records: {len(all_nnpc)}')
print()

# For each of the 5 missing merchants, check ALL fields for token matches
missing = {
    'POWERFOIL': ['POWER', 'FOIL', 'POWERFOIL'],
    'FENCHURCH': ['FEN', 'CHURCH', 'FENCHURCH'],
    'NEWHEALTH': ['NEW', 'HEALTH', 'NEWHEALTH'],
    'ROSEFUN': ['ROSE', 'FUN', 'ROSEFUN'],
    'OLWADAMS': ['OLUWA', 'DAMS', 'OLUWADAMS', 'ADAMS', 'OLWADAMS'],
}

for merchant, tokens in missing.items():
    print('=' * 80)
    print('SEARCHING: ' + merchant)
    print('Tokens: ' + str(tokens))
    print('=' * 80)
    
    found_rows = set()
    
    for row in all_nnpc:
        name = str(row[0] or '').upper()
        slip = str(row[3] or '').upper()
        acct = str(row[4] or '').upper()
        contact = str(row[5] or '').upper()
        phone = str(row[6] or '')
        email = str(row[2] or '').upper()
        
        all_text = name + ' ' + slip + ' ' + acct + ' ' + contact + ' ' + email
        
        match_tokens = []
        for token in tokens:
            t_upper = token.upper()
            if t_upper in name or t_upper in slip or t_upper in acct or t_upper in contact or t_upper in email:
                match_tokens.append(token)
        
        if match_tokens:
            row_key = (name, str(row[1] or ''))
            if row_key not in found_rows:
                found_rows.add(row_key)
                print('  [TOKEN] ' + ', '.join(match_tokens))
                print('          Name: ' + name[:55] + '  MX=' + str(row[1] or '')[:12] + '  [' + str(row[7] or '')[:20] + ']')
                if email:
                    print('          Email: ' + email[:35])
                if contact:
                    print('          Contact: ' + contact[:30])
    
    if not found_rows:
        print('  NOT FOUND - no token matches in any NNPC record')
    
    # Count similar merchants by synonym
    syn_map = {
        'POWERFOIL': 'POWER',
        'OLWADAMS': 'PETROLEUM',
        'NEWHEALTH': 'HEALTH',
    }
    if merchant in syn_map:
        syn = syn_map[merchant]
        syn_count = 0
        syn_names = set()
        for row in all_nnpc:
            name_upper = str(row[0] or '').upper()
            if syn.upper() in name_upper:
                syn_names.add((name_upper[:55], str(row[1] or '')[:12]))
        if syn_names:
            print()
            print('  Similar by synonym "' + syn + '": ' + str(len(syn_names)) + ' merchants')
            for sname, smx in sorted(list(syn_names)[:10]):
                print('    ' + sname + '  MX=' + smx)
    
    print()

db.close()
print('DONE.')
