import pandas as pd
from pathlib import Path

folder = Path(r'C:\Users\David.Olamijulo\downloads\parameter') / 'data'

nnpc_files = [
    folder / 'NNPC PARAMETER FILE BATCH .xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 1.xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 2.xlsx',
    folder / 'NNPC PARAMETER FILE BATCH 4.xlsx',
    folder / 'NNpc parameter master.xlsx',
]

for nf in nnpc_files:
    if not nf.exists():
        print(f'[MISSING] {nf.name}')
        continue
    
    try:
        xls = pd.ExcelFile(str(nf))
    except Exception as e:
        print(f'[ERROR] {nf.name}: {e}')
        continue
    
    print(f'\n{"="*80}')
    print(f'  {nf.name} ({nf.stat().st_size/1024:.0f} KB) — {len(xls.sheet_names)} sheets')
    print(f'{"="*80}')
    
    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
            df = df.dropna(axis=1, how='all')
        except Exception as e:
            print(f'\n  ❌ Sheet: {sheet} — ERROR: {e}')
            continue
        
        print(f'\n  --- Sheet: {sheet} ({len(df)} rows, {len(df.columns)} cols) ---')
        print(f'  Columns:')
        for col in df.columns:
            # Show first non-empty value
            sample = ''
            for idx in range(min(len(df), 5)):
                v = str(df[col].iloc[idx]).strip()
                if v and v != 'nan':
                    sample = v[:60]
                    break
            print(f'    {str(col):<40} Sample: {sample}')
        
        # Show first 3 rows as CSV
        print(f'  First 3 rows:')
        for idx in range(min(3, len(df))):
            vals = []
            for col in df.columns:
                v = str(df[col].iloc[idx]).strip()
                if v and v != 'nan':
                    vals.append(f'{str(col)}={v[:30]}')
            print(f'    Row {idx+2}: {", ".join(vals)}')
