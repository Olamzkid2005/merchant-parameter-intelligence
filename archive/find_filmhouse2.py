import pandas as pd
file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

# Check the NIBSS raw column names first (small sheet)
df2 = pd.read_excel(xls, sheet_name='2ISW NIBSS FORMAT', nrows=1)
print("NIBSS FORMAT columns:")
for i, c in enumerate(df2.columns):
    print(f"  [{i}] {repr(c)}")

# Check specific few sheets for film house
for s in ['2ISW_Parameter', '2ISW NIBSS FORMAT', 'Sameday_Settlement_Merchants', 'Change of merchant details']:
    df = pd.read_excel(xls, sheet_name=s)
    df = df.dropna(axis=1, how='all')
    found = False
    for col in df.columns:
        matches = df[col].astype(str).str.contains('film.?house', case=False, regex=True, na=False)
        count = matches.sum()
        if count > 0:
            if not found:
                print(f"\n[{s}] Found film house:")
                found = True
            examples = df.loc[matches.index[:5], col].tolist()
            print(f"  col={repr(col)[:60]} x{count}: {examples}")
