"""
Scan Excel files in the downloads folder for the 12 missing merchants.
Focus on files likely to contain merchant/parameter data.
"""
import sys
import pandas as pd
from pathlib import Path

MISSING = [
    "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
    "FENCHURCH SERVICES LIMITED",
    "G&G MULTISERVICES INVESTMENT LIMITED",
    "LAGOON WATERS LTD",
    "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
    "MONEYTRUST MICROFINANACE BANK LTD",
    "MUSSAN OIL NIGERIA LIMITED",
    "NEWHEALTH PHARMACY LTD 3",
    "NWANERI VICTOR",
    "OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED",
    "POWERFOIL GLOBAL SERVICES LIIMITED",
    "ROSEFUN VENTURES",
]

# Files most likely to contain merchant parameter data (larger, merchant-named)
TARGET_FILES = [
    "2ISW_Parameter_File 5.xlsx",  # Already searched, but include for completeness
    "Approved_QTB_Merchant_details_V3.xlsx",
    "Approved_QTB_Merchant_details_V3 (1).xlsx",
    "Book2 (1) (1).xlsx",
    "Book2 (1) (2).xlsx",
    "Book2 (1).xlsx",
    "Book2 (10).xlsx",
    "Book2 (10) (1) (1).xlsx",
    "Book2 (10) (1) (2).xlsx",
    "Book2 (10) (1) (3) (1).xlsx",
    "Book2 (10) (1) (3).xlsx",
    "Book2 (10) (1).xlsx",
    "Book2 1.xlsx",
    "Book2.xlsx",
    "BSP Feedback_7th June 2026.xlsx",
    "CONFIGURATION AND DEPLOYMENT.xlsx",
    "Monday (1) (1).xlsx",
    "Monday (1).xlsx",
    "Monday.xlsx",
    "Tuesday.xlsx",
    "Wednesday.xlsx",
    "Wednesday (1).xlsx",
    "Friday.xlsx",
    "Terminal Registered Database ver1.xlsx",
    "Terminal Registered Database.xlsx",
]

base = Path(r"C:\Users\David.Olamijulo\downloads")

# Build keyword search terms from missing merchants
def get_keywords(name):
    """Extract searchable keywords from a merchant name."""
    import re
    name = name.upper()
    name = re.sub(r'[^\w\s]', ' ', name)
    words = set(name.split())
    # Remove generic words
    generic = {"LIMITED", "LTD", "NIGERIA", "AND", "THE", "SERVICES", 
               "ENTERPRISES", "INVESTMENT", "COMPANY", "GROUP", "PLC",
               "ACCOUNT", "COLLECTION", "REVENUE", "OIL", "GAS", "RESOURCES",
               "PHARMACY", "LTD", "LIIMITED", "GLOBAL", "MICROFINANCE", "BANK"}
    return words - generic

merchant_keywords = {}
for m in MISSING:
    merchant_keywords[m] = get_keywords(m)

print(f"Scanning {len(TARGET_FILES)} Excel files for 12 missing merchants...")
print()

for filename in TARGET_FILES:
    filepath = base / "parameter" / filename
    if not filepath.exists():
        filepath = base / filename
    if not filepath.exists():
        continue
    
    print(f"  [{filename[:45]:45s}] ", end="", flush=True)
    
    try:
        xls = pd.ExcelFile(str(filepath))
        sheets = xls.sheet_names
        found_any = False
        
        for sheet in sheets:
            df = pd.read_excel(xls, sheet_name=sheet)
            df_str = df.astype(str).apply(lambda x: x.str.upper(), axis=1)
            
            for merchant, keywords in merchant_keywords.items():
                if not keywords:
                    continue
                # Check if any keyword appears in any cell of this sheet
                for kw in keywords:
                    if len(kw) < 3:
                        continue
                    mask = df_str.apply(lambda col: col.str.contains(kw, na=False))
                    if mask.any().any():
                        # Found a match - get the row
                        rows = df_str[mask.any(axis=1)]
                        # Find which cell matched
                        for idx in rows.index[:3]:
                            row_data = df.iloc[idx]
                            for col in df.columns:
                                val = str(row_data[col]).upper()
                                if kw in val:
                                    print(f"\\n    [{merchant[:45]}] Found '{kw}' in [{sheet}] {col} = '{str(row_data[col])[:60]}'")
                                    found_any = True
                                    break
                        break  # Found one keyword match for this merchant
        
        if not found_any:
            print("no matches")
        else:
            print()
    except Exception as e:
        print(f"ERROR: {e}")

print()
print("Done!")
