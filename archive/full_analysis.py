import pandas as pd
import numpy as np

file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

sheet_names = xls.sheet_names
print(f"All sheet names: {sheet_names}")

# ============================================================
# Sheet 1: 2ISW_Parameter
# ============================================================
print("="*80)
print("SHEET 1: 2ISW_Parameter - Deep Dive")
print("="*80)
df = pd.read_excel(xls, sheet_name='2ISW_Parameter')

# Drop all-NaN columns
df_clean = df.dropna(axis=1, how='all')

# Clean column names
col_map = {}
for c in df_clean.columns:
    cleaned = str(c).replace('\xa0', '').strip()
    col_map[c] = cleaned
df_clean = df_clean.rename(columns=col_map)

print(f"Cleaned columns ({len(df_clean.columns)}):")
for i, c in enumerate(df_clean.columns):
    print(f"  [{i}] {c}")

# State code
if 'stateCode' in df_clean.columns:
    print("\nState Code Distribution (top 15):")
    print(df_clean['stateCode'].value_counts().head(15).to_string())

# Terminal model
if 'terminalModelDescription' in df_clean.columns:
    print("\nTerminal Model Distribution:")
    print(df_clean['terminalModelDescription'].value_counts().to_string())

# App version
if 'appVersion' in df_clean.columns:
    print("\nApp Version Distribution:")
    print(df_clean['appVersion'].value_counts().to_string())

# Terminal type
if 'terminalType' in df_clean.columns:
    print("\nTerminal Type Distribution:")
    print(df_clean['terminalType'].value_counts().to_string())

# BVN analysis
if 'bvn' in df_clean.columns:
    bvn = df_clean['bvn'].astype(str)
    total = len(bvn)
    default_bvn = (bvn == '99999999999').sum()
    freq_bvn = bvn.value_counts().head(10)
    print(f"\nBVN: {total} records")
    print(f"Default BVN (99999999999): {default_bvn} ({default_bvn/total*100:.1f}%)")

# Email domain analysis
if 'email' in df_clean.columns:
    emails = df_clean['email'].dropna().astype(str)
    domains = emails.str.split('@').str[1].str.lower().value_counts()
    print(f"\nEmail Domains (top 15):")
    print(domains.head(15).to_string())

# ============================================================
# Sheet 2: 2ISW NIBSS FORMAT
# ============================================================
print("\n\n" + "="*80)
print("SHEET 2: 2ISW NIBSS FORMAT")
print("="*80)
df_nibss = pd.read_excel(xls, sheet_name='2ISW NIBSS FORMAT')
df_nibss_clean = df_nibss.dropna(axis=1, how='all')
print(f"Rows: {len(df_nibss_clean)}, Columns: {len(df_nibss_clean.columns)}")
print(f"Columns: {list(df_nibss_clean.columns)}")
print("First 3 rows:")
print(df_nibss_clean.head(3).to_string())

# ============================================================
# Cross-sheet analysis
# ============================================================
print("\n\n" + "="*80)
print("CROSS-SHEET ANALYSIS")
print("="*80)

# Get TIDs from each sheet
param_tids = set(df_clean.get('terminalId', pd.Series()).dropna().unique())
print(f"TIDs in 2ISW_Parameter: {len(param_tids)}")

# Sameday Settlement
df_sameday = pd.read_excel(xls, sheet_name='Sameday_Settlement_Merchants')
# Find terminalId column in sameday
sameday_cols = [c for c in df_sameday.columns if 'terminal' in str(c).lower()]
print(f"Sameday terminal columns: {sameday_cols}")
if sameday_cols:
    tid_col = sameday_cols[0]
    sameday_tids = set(df_sameday[tid_col].dropna().unique())
    common = param_tids & sameday_tids
    only_param = param_tids - sameday_tids
    only_sameday = sameday_tids - param_tids
    print(f"\nSameday TIDs: {len(sameday_tids)}")
    print(f"  In both: {len(common)}")
    print(f"  Only in Parameter: {len(only_param)}")
    print(f"  Only in Sameday: {len(only_sameday)}")

# Deactivated TIDs
df_dt = pd.read_excel(xls, sheet_name='Deactivated TID')
deactivated_tids = set(df_dt['TERMINAL ID'].dropna().unique())
print(f"\nDeactivated TIDs: {len(deactivated_tids)}")
active_in_param = param_tids - deactivated_tids
deactivated_in_param = param_tids & deactivated_tids
print(f"  Active in Parameter File: {len(active_in_param)}")
print(f"  Deactivated but still in Parameter File: {len(deactivated_in_param)}")

# Deployment Status
df_ds = pd.read_excel(xls, sheet_name='Deployment Status _Sim details')
print(f"\nDeployment Status:")
print(df_ds['Deployment Status'].value_counts().to_string())

deploy_tids = set(df_ds['Terminal ID'].dropna().unique())
missing_from_param = deploy_tids - param_tids
print(f"TIDs deployed but NOT in Parameter File: {len(missing_from_param)}")

# Change of merchant details
df_chg = pd.read_excel(xls, sheet_name='Change of merchant details')
print(f"\nChange of Merchant Details records: {len(df_chg)}")
if 'STATE CODE' in df_chg.columns:
    print("State distribution:")
    print(df_chg['STATE CODE'].value_counts().head(10).to_string())

# ETT Analysis
df_ett = pd.read_excel(xls, sheet_name='ETT')
print(f"\nETT (Extended Transaction Types): {len(df_ett)} fee tiers")
print(df_ett[['Description', 'Amount_to_Merchant', 'Amount_to_Acquirer']].to_string())

# State Code mapping
df_state = pd.read_excel(xls, sheet_name='State Code')
state_map = df_state[df_state['State Code'].notna() & (df_state['State Code'] != 'State Code')]
print(f"\nState Code mapping ({len(state_map)} states):")
print(state_map[['State Code', 'State', 'Status']].to_string())
