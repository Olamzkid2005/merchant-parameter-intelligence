"""
Fast vectorized search of ALL sheets in the parameter file for 12 missing merchants.
"""
import pandas as pd
from pathlib import Path

xls_path = Path(r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx")
xls = pd.ExcelFile(str(xls_path))

# Merchants to search for (with alternative keywords to check)
CHECKS = {
    "CRANE FIELD": ["CRANE", "FIELD", "INTERNMATIONAL", "JEDDO"],
    "FENCHURCH": ["FENCHURCH", "FEN CHURCH"],
    "G&G MULTISERVICES": ["MULTISERVICES", "G&G"],
    "LAGOON": ["LAGOON", "LAGOON"],
    "MARYLAND MALL": ["MARYLAND MALL", "SWEB MARYLAND", "MARYLAND"],
    "MONEYTRUST": ["MONEYTRUST", "MONEY TRUST"],
    "MUSSAN": ["MUSSAN", "MUSAN"],
    "NEWHEALTH": ["NEWHEALTH", "NEW HEALTH"],
    "NWANERI": ["NWANERI", "IKATI"],
    "OLWADAMS": ["OLWADAMS", "OLUWADAMS"],
    "POWERFOIL": ["POWERFOIL"],
    "ROSEFUN": ["ROSEFUN", "ROSE FUN"],
}

print(f"Searching {len(xls.sheet_names)} sheets for 12 merchants...")
print()

for sheet in xls.sheet_names:
    try:
        df = pd.read_excel(xls, sheet_name=sheet, dtype=str)
        df = df.fillna("")
        # Convert all to uppercase for case-insensitive search
        df_upper = df.apply(lambda col: col.str.upper())
        
        found_in_sheet = False
        
        for merchant, keywords in CHECKS.items():
            found = False
            for kw in keywords:
                if len(kw) < 3:
                    continue
                # Vectorized: check every cell in the dataframe for this keyword
                mask = df_upper.apply(lambda col: col.str.contains(kw, na=False, regex=False))
                if mask.any().any():
                    # Get first match location
                    for col_idx, col in enumerate(df.columns):
                        col_mask = mask.iloc[:, col_idx]
                        if col_mask.any():
                            row_idx = col_mask.idxmax()
                            val = str(df.iloc[row_idx, col_idx])
                            if not found_in_sheet:
                                found_in_sheet = True
                            found = True
                            print(f"  [{sheet:35s}] {merchant:30s} | col={col[:25]:25s} | row={row_idx+2:5d} | val='{val[:60]}'")
                            break
                    if found:
                        break
        
        if not found_in_sheet:
            print(f"  [{sheet:35s}] (no matches)")
    except Exception as e:
        print(f"  [{sheet:35s}] ERROR: {e}")

print()
print("Done!")
