import pandas as pd
file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

# Check for THE FILM HOUSE
for s in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=s)
    df = df.dropna(axis=1, how='all')
    for col in df.columns:
        col_str = str(col).replace('\xa0','').strip().lower()
        for idx, val in df[col].dropna().items():
            if isinstance(val, str) and ('film house' in val.lower() or 'filmhouse' in val.lower()):
                print(f"[{s}] col={repr(col)} row={idx+2}: {val[:120]}")

print("\n\n=== NIBSS FORMAT sheet raw column names ===")
df2 = pd.read_excel(xls, sheet_name='2ISW NIBSS FORMAT')
for i, c in enumerate(df2.columns):
    print(f"  [{i}] repr={repr(c)} | bytes={c.encode('utf-8', errors='replace')[:50]}")
