"""Verify disputed TIDs against the RAW 2ISW_Parameter_File 5.xlsx workbook."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import pandas as pd

XLSX = 'data/2ISW_Parameter_File 5.xlsx'
WANT = {
    '2ISWW054': 'OASIS CENTER',
    '2ISWM151': 'BELA VISTA (39 LEKKI ESTATE)',
    '2ISW7793': 'FRESHFORTE',
    '2ISW7951': 'FRESHFORTE',
    '2ISWZ318': 'MARINA',
    '2ISWI393': 'PROVIDENCE PLAZA',
    '2ISW2816': 'BRITISH INT\'L SCHOOL',
    '2ISW2841': 'BRITISH INT\'L SCHOOL',
}
# Also check Medplus.xlsx does NOT contain them
med = pd.read_excel('data/Medplus.xlsx', dtype=str, keep_default_na=False, header=0)
med_tids = set(str(r['TERMINAL ID']).strip() for _, r in med.iterrows())
print("=== Medplus.xlsx membership of disputed TIDs ===")
for t in WANT:
    print(f"  {t} in Medplus.xlsx: {t in med_tids}")

xls = pd.ExcelFile(XLSX)
print(f"\n=== RAW {XLSX} — sheets: {len(xls.sheet_names)} ===")
for sheet in xls.sheet_names:
    raw = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False, header=None)
    for tid, label in WANT.items():
        # search every cell in the sheet for the TID
        mask = raw.apply(lambda col: col.astype(str).str.contains(tid, na=False, regex=False))
        hit_rows = mask.any(axis=1)
        idxs = raw.index[hit_rows].tolist()
        if idxs:
            print(f"\n  [{sheet}] TID {tid} ({label}) — found in row(s) {idxs[:5]}")
            for i in idxs[:2]:
                row_vals = [str(v).strip() for v in raw.iloc[i].tolist() if str(v).strip()]
                # print the row's key cells: first 8 non-empty values
                print(f"      row {i}: {row_vals[:10]}")
