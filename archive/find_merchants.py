import pandas as pd
import re
file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"
xls = pd.ExcelFile(file)

searches = ['FILM HOUSE', 'ARTEE', 'BEACONHEALTH', 'RUBELS', 'SPAR LEKKI', 'IMAX', 'BEACON HEALTH']

for s in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=s)
    df = df.dropna(axis=1, how='all')
    for col in df.columns:
        col_str = str(col).replace('\xa0','').strip().lower()
        for search in searches:
            for idx, val in df[col].dropna().items():
                if isinstance(val, str) and search.lower() in val.lower():
                    print(f"[{s}] col={col} row={idx+2}: {val[:100]}")
                    break
