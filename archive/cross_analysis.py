import pandas as pd
import numpy as np

file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

# Sheet 1: Parameter File - focused analysis
print("="*80)
print("SHEET 1: Parameter File - Deep Dive")
print("="*80)
# Find the parameter sheet name (first sheet)
sheet_names = xls.sheet_names
print(f"All sheet names: {sheet_names}")
param_sheet = [s for s in sheet_names if 'parameter' in s.lower() or 'isw' in s.lower()][0]
print(f"Using sheet: {param_sheet}")
df = pd.read_excel(xls, sheet_name=param_sheet)
df_clean = df.dropna(axis=1, how='all')

# Rename columns - clean up non-breaking spaces
col_map = {}
for c in df_clean.columns[:40]:
    cleaned = str(c).replace('\xa0', '').strip()
    col_map[c] = cleaned
df_clean = df_clean.rename(columns=col_map)

# Show actual column names after cleaning
print(f"\nCleaned columns: {list(df_clean.columns)}")

# State code distribution
if 'stateCode' in df_clean.columns:
    print("\nState Code Distribution:")
    print(df_clean['stateCode'].value_counts().to_string())

# Terminal model distribution  
if 'terminalModelDescription' in df_clean.columns:
    print("\nTerminal Model Distribution:")
    print(df_clean['terminalModelDescription'].value_counts().to_string())

# App version distribution
if 'appVersion' in df_clean.columns:
    print("\nApp Version Distribution:")
    print(df_clean['appVersion'].value_counts().to_string())

# Terminal type distribution
if 'terminalType' in df_clean.columns:
    print("\nTerminal Type Distribution:")
    print(df_clean['terminalType'].value_counts().to_string())

# Check BVN patterns
if 'bvn' in df_clean.columns:
    bvn = df_clean['bvn'].astype(str)
    unique_bvns = bvn.nunique()
    total = len(bvn)
    print(f"\nBVN: {total} records, {unique_bvns} unique")
    default_bvn = (bvn == '99999999999').sum()
    print(f"Default BVN (99999999999): {default_bvn} ({default_bvn/total*100:.1f}%)")
    placeholder_bvn = (bvn.str.startswith('999')).sum()
    print(f"Placeholder BVN (999*): {placeholder_bvn} ({placeholder_bvn/total*100:.1f}%)")

# Check duplicates in parameter file
dup_tids = df_clean[df_clean.duplicated(subset=['terminalId'], keep=False)]
if len(dup_tids) > 0:
    print(f"\nDuplicate terminalIds in Parameter File: {dup_tids['terminalId'].nunique()} unique TIDs")
    tid_counts = df_clean['terminalId'].value_counts()
    multi_tids = tid_counts[tid_counts > 1]
    print(f"TIDs appearing multiple times:\n{multi_tids.head(20).to_string()}")

# Sheet 2: Sameday Settlement
print("\n\n" + "="*80)
print("SHEET 2: Sameday Settlement Merchants - Analysis")
print("="*80)
df2 = pd.read_excel(xls, sheet_name='Sameday_Settlement_Merchants')
df2_clean = df2.dropna(axis=1, how='all')

# Settlement type analysis
if 'Settlement Type' in df2_clean.columns:
    print("\nSettlement Type:")
    print(df2_clean['Settlement Type'].value_counts().to_string())

# Compare terminalIds between Parameter File and Sameday
param_tids = set(df_clean['terminalId'].dropna().unique())
sameday_tids = set(df2_clean['terminalId'].dropna().unique())

common = param_tids & sameday_tids
only_param = param_tids - sameday_tids
only_sameday = sameday_tids - param_tids

print(f"\nTerminal ID Overlap:")
print(f"  In both Parameter File & Sameday: {len(common)}")
print(f"  Only in Parameter File: {len(only_param)}")
print(f"  Only in Sameday: {len(only_sameday)}")

# Compare with Deactivated TIDs
df_dt = pd.read_excel(xls, sheet_name='Deactivated TID')
deactivated_tids = set(df_dt['TERMINAL ID'].dropna().unique())

still_active_in_param = param_tids - deactivated_tids
deactivated_in_param = param_tids & deactivated_tids

print(f"\nDeactivated TID Analysis:")
print(f"  Active TIDs in Parameter File: {len(still_active_in_param)}")
print(f"  Deactivated TIDs still in Parameter File: {len(deactivated_in_param)}")

# Deployment status
df_ds = pd.read_excel(xls, sheet_name='Deployment Status _Sim details')
print(f"\n\nDeployment Status Distribution:")
print(df_ds['Deployment Status'].value_counts().to_string())

# Check for TIDs in deployment that aren't in parameter file
deploy_tids = set(df_ds['Terminal ID'].dropna().unique())
missing_from_param = deploy_tids - param_tids
print(f"\nTIDs in Deployment but NOT in Parameter File: {len(missing_from_param)}")
if len(missing_from_param) > 0:
    missing_df = df_ds[df_ds['Terminal ID'].isin(missing_from_param)]
    print(f"  Examples:")
    print(f"  {missing_df[['Terminal ID', 'Merchant Name', 'Deployment Status']].head(10).to_string()}")

# Change of merchant details - what's changing
df_chg = pd.read_excel(xls, sheet_name='Change of merchant details')
print(f"\n\nChange of Merchant Details - State Code Distribution:")
sc = df_chg['STATE CODE'].value_counts()
print(sc.head(10).to_string())

# PTSP distribution across sheets
print(f"\n\nPTSP Distribution in Parameter File:")
ptsp = df_clean['ptspCode'].value_counts()
print(ptsp.to_string())

print(f"\nPTSP in Change of Merchant Details:")
ptsp2 = df_chg['PTSP'].value_counts()
print(ptsp2.to_string())

# Data quality: email patterns in Parameter File
print(f"\n\nEmail Domain Analysis (Parameter File):")
emails = df_clean['email'].dropna().astype(str)
domains = emails.str.split('@').str[1].str.lower().value_counts()
print(domains.head(15).to_string())

# Mobile phone analysis
print(f"\n\nMobile Phone length analysis:")
phones = df_clean['mobilePhone'].dropna().astype(str)
phone_lens = phones.str.len().value_counts().sort_index()
print(phone_lens.to_string())
