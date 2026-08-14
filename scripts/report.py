"""
report.py — Phase 9: Merchant Intelligence Report builder.

Input:  a list of merchant names.
Output: a dict of DataFrames ready for a multi-sheet Excel workbook:

  - Summary             — counts and match-rate statistics
  - Exact Matches       — best-scoring DB record per merchant (Exact Match)
  - High Confidence     — High Confidence / Alias Match records
  - Possible Matches    — Possible Match records
  - Emails              — every email recovered for matched merchants
  - Phone Numbers       — every phone recovered for matched merchants
  - Contacts            — contact names + phones + addresses
  - Addresses           — addresses recovered for matched merchants
  - Duplicate Merchants — same merchant name appearing across multiple rows/sheets
  - Merchants Not Found — merchants with no confident match

Usage (CLI):
  python scripts/report.py "THE FILM HOUSE LIMITED" "SPAR Lekki" -o report.xlsx
  python scripts/report.py --list merchants.txt -o report.xlsx

The same build_report() is exposed over HTTP by api.py (/api/report,
/api/report/export) so the React UI can build the report in-browser.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config, MerchantSearch

SHEET_ORDER = [
    "summary", "exact", "high", "possible", "emails", "phones",
    "contacts", "addresses", "duplicates", "not_found",
]

SHEET_NAMES = {
    "summary": "Summary",
    "exact": "Exact Matches",
    "high": "High Confidence",
    "possible": "Possible Matches",
    "emails": "Emails",
    "phones": "Phone Numbers",
    "contacts": "Contacts",
    "addresses": "Addresses",
    "duplicates": "Duplicate Merchants",
    "not_found": "Merchants Not Found",
}


def _classify(match_type: str) -> str:
    """Map a match type to one of the report's tier sheets.

    Callers gate on overall_score >= POSSIBLE_THRESHOLD before classifying,
    so the only reachable tiers are exact / high / possible.
    """
    mt = (match_type or "").lower()
    if "exact" in mt:
        return "exact"
    if "high" in mt or "alias" in mt:
        return "high"
    return "possible"


def build_report(merchants: List[str],
                 top_n: int = 3,
                 searcher: Optional[MerchantSearch] = None) -> Dict[str, Any]:
    """Run the full Phase 9 report for a list of merchant names.

    Returns dict of DataFrames keyed by SHEET_ORDER. Every sheet is always
    present (empty DataFrames when nothing was found).
    """
    searcher = searcher or MerchantSearch()

    rows: Dict[str, List[Dict[str, Any]]] = {
        "exact": [], "high": [], "possible": [],
        "not_found": [], "emails": [], "phones": [],
        "contacts": [], "addresses": [],
    }

    for merchant in merchants:
        merchant = (merchant or "").strip()
        if not merchant:
            continue

        results = searcher.search(merchant, limit=top_n)
        best = results[0] if results else None

        if best and best.overall_score >= config.POSSIBLE_THRESHOLD:
            rec = best.record
            tier = _classify(best.match_type)
            row = {
                "Merchant (input)": merchant,
                "Best Match": rec.get("merchant_name", ""),
                "Score": round(best.overall_score / 10, 1),
                "Match Type": best.match_type,
                "TID": rec.get("tid", ""),
                "MX Code": rec.get("mxcode", ""),
                "Email": rec.get("email", ""),
                "Phone": rec.get("phone", ""),
                "Contact Name": rec.get("contact_name", ""),
                "Address": rec.get("address", ""),
                "Account Name": rec.get("account_name", ""),
                "Sheet": rec.get("sheet_name", ""),
                "Row": rec.get("row_number", ""),
            }
            rows[tier].append(row)
            if rec.get("email"):
                rows["emails"].append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Email": rec.get("email", ""),
                })
            if rec.get("phone"):
                rows["phones"].append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Phone": rec.get("phone", ""),
                })
            if rec.get("phone") or rec.get("contact_name") or rec.get("address"):
                rows["contacts"].append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Contact Name": rec.get("contact_name", ""),
                    "Phone": rec.get("phone", ""),
                    "Address": rec.get("address", ""),
                })
            if rec.get("address"):
                rows["addresses"].append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Address": rec.get("address", ""),
                })
        else:
            rows["not_found"].append({
                "Merchant (input)": merchant,
                "Closest Candidate": (best.record.get("merchant_name", "")
                                      if best else ""),
                "Score": (round(best.overall_score / 10, 1) if best else 0),
            })

    # Duplicate detection: same merchant_name appearing on >1 DB row
    # (across sheets/files). Group by UPPER(merchant_name) so case variants
    # collapse into one cluster.
    dup_rows: List[Dict[str, Any]] = []
    matched_names = [r["Best Match"] for r in rows["exact"] + rows["high"]
                     + rows["possible"]]
    if matched_names:
        try:
            conn = sqlite3.connect(str(config.active_db()))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            seen_names = sorted({n for n in matched_names if n})
            for name in seen_names:
                occ = c.execute(
                    "SELECT id, merchant_name, sheet_name, row_number, tid, "
                    "mxcode, email FROM merchants "
                    "WHERE UPPER(merchant_name) = UPPER(?) ORDER BY sheet_name",
                    (name,),
                ).fetchall()
                if len(occ) > 1:
                    loc = ", ".join(
                        f"{o['sheet_name'] or '?'} row {o['row_number'] or '?'}"
                        for o in occ
                    )
                    dup_rows.append({
                        "Merchant": occ[0]["merchant_name"],
                        "Occurrences": len(occ),
                        "Locations": loc,
                        "TIDs": ", ".join(str(o["tid"]) for o in occ if o["tid"]),
                        "MX Codes": ", ".join(str(o["mxcode"]) for o in occ if o["mxcode"]),
                    })
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass

    # Summary sheet
    n_found = (len(rows["exact"]) + len(rows["high"]) + len(rows["possible"]))
    n_total = n_found + len(rows["not_found"])
    summary = [
        {"Metric": "Total merchants", "Value": n_total},
        {"Metric": "Exact matches", "Value": len(rows["exact"])},
        {"Metric": "High confidence", "Value": len(rows["high"])},
        {"Metric": "Possible matches", "Value": len(rows["possible"])},
        {"Metric": "Not found", "Value": len(rows["not_found"])},
        {"Metric": "Match rate",
         "Value": f"{n_found / n_total * 100:.1f}%" if n_total else "—"},
        {"Metric": "Emails recovered", "Value": len(rows["emails"])},
        {"Metric": "Phones recovered", "Value": len(rows["phones"])},
        {"Metric": "Contacts recovered", "Value": len(rows["contacts"])},
        {"Metric": "Duplicate merchant clusters", "Value": len(dup_rows)},
        {"Metric": "Generated", "Value": datetime.now().strftime("%Y-%m-%d %H:%M")},
    ]

    report = {k: pd.DataFrame(v) for k, v in rows.items()}
    report["summary"] = pd.DataFrame(summary)
    report["duplicates"] = pd.DataFrame(dup_rows)
    return report


def export_report(report: Dict[str, Any], out_path: Path):
    """Write the report DataFrames to an Excel workbook (all sheets)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        for key in SHEET_ORDER:
            df = report[key]
            if not df.empty:
                df.to_excel(writer, sheet_name=SHEET_NAMES[key], index=False)
            else:
                pd.DataFrame({"Note": ["No records"]}).to_excel(
                    writer, sheet_name=SHEET_NAMES[key], index=False
                )
    print(f"[OK] Report written to: {out_path}")


def _load_merchants(args) -> List[str]:
    merchants: List[str] = []
    if args.list:
        for line in Path(args.list).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "//")):
                merchants.append(line)
    if args.excel:
        df = pd.read_excel(args.excel, sheet_name=args.sheet) if args.sheet else pd.read_excel(args.excel)
        col = args.col or df.columns[0]
        merchants.extend(str(v).strip() for v in df[col].dropna())
    if args.names:
        merchants.extend(args.names)
    seen = set()
    unique = []
    for m in merchants:
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Merchant Intelligence Report builder")
    parser.add_argument("names", nargs="*", help="Merchant names to report on")
    parser.add_argument("--list", help="Text file with one merchant per line")
    parser.add_argument("--excel", help="Excel file with merchant names")
    parser.add_argument("--sheet", default=None, help="Sheet name in --excel file")
    parser.add_argument("--col", default=None, help="Column name in --excel file")
    parser.add_argument("-o", "--output", default="reports/Merchant_Intelligence_Report.xlsx",
                        help="Output Excel path")
    parser.add_argument("--top", type=int, default=3, help="Top-N candidates per merchant")
    args = parser.parse_args()

    merchants = _load_merchants(args)
    if not merchants:
        parser.print_help()
        print("\nNo merchant names provided.")
        return

    print(f"Building report for {len(merchants)} merchants...")
    report = build_report(merchants, top_n=args.top)
    export_report(report, args.output)
    print("\n-- Summary --")
    print(report["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
