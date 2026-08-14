import pandas as pd
import numpy as np

file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

all_sheets = xls.sheet_names
print(f"Total sheets: {len(all_sheets)}")
print(f"Sheet names: {all_sheets}")
print("="*80)

for sheet in all_sheets:
    print(f"\n{'='*80}")
    print(f"  SHEET: {sheet}")
    print(f"{'='*80}")
    
    # Read data - skip excessive None columns
    df = pd.read_excel(xls, sheet_name=sheet)
    
    # Drop fully empty columns
    df_clean = df.dropna(axis=1, how='all')
    
    print(f"  Rows: {len(df_clean)}, Columns: {len(df_clean.columns)} (dropped {len(df.columns) - len(df_clean.columns)} all-NaN cols)")
    print(f"  Columns: {list(df_clean.columns)}")
    print(f"  Memory usage: {df_clean.memory_usage(deep=True).sum() / 1024:.1f} KB")
    
    # Basic info
    print(f"\n  --- Data Types ---")
    print(f"  {df_clean.dtypes.to_string()}")
    
    # Missing values
    missing = df_clean.isnull().sum()
    missing_pct = (missing / len(df_clean)) * 100
    missing_info = pd.DataFrame({'Missing': missing, '%': missing_pct})
    missing_info = missing_info[missing_info['Missing'] > 0]
    if len(missing_info) > 0:
        print(f"\n  --- Missing Values ---")
        print(f"  {missing_info.to_string()}")
    else:
        print(f"\n  --- No missing values ---")
    
    # Numeric summary
    num_cols = df_clean.select_dtypes(include=[np.number]).columns
    if len(num_cols) > 0:
        print(f"\n  --- Numeric Summary ---")
        desc = df_clean[num_cols].describe().T
        desc['range'] = desc['max'] - desc['min']
        print(f"  {desc.to_string()}")
    
    # Categorical / object columns - value counts
    cat_cols = df_clean.select_dtypes(include=['object']).columns
    for col in cat_cols[:5]:  # first 5 only to keep output manageable
        unique_vals = df_clean[col].nunique()
        if unique_vals < 30 and unique_vals > 1:
            print(f"\n  --- {col} (unique: {unique_vals}) ---")
            print(f"  {df_clean[col].value_counts().to_string()}")
    
    # Duplicate rows check
    dup_rows = df_clean.duplicated(keep=False).sum()
    if dup_rows > 0:
        print(f"\n  --- Duplicates ---")
        print(f"  Duplicate rows: {dup_rows}")
        if 'terminalId' in df_clean.columns:
            dup_tids = df_clean[df_clean.duplicated(subset=['terminalId'], keep=False)]
            print(f"  Duplicate terminalIds: {dup_tids['terminalId'].nunique()} unique")
    
    print()
