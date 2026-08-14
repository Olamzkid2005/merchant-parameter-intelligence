"""
reconcile.py — Merchant reconciliation workflow.

Input:  a list of merchant names (CLI args, text file, or Excel column).
Output: an Excel report with sheets:

  - Summary          — counts and match-rate statistics
  - Matches          — best-scoring DB record per merchant
  - Not Found        — merchants with no confident match
  - All Results      — every scored candidate (top-N per merchant)
  - Emails           — all emails found for matched merchants
  - Contacts         — phones / contact names / addresses

Usage:
  python scripts/reconcile.py "THE FILM HOUSE LIMITED" "SPAR Lekki" -o report.xlsx
  python scripts/reconcile.py --list merchants.txt -o report.xlsx
  python scripts/reconcile.py --excel input.xlsx --sheet Sheet1 --col "Merchant name" -o report.xlsx
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows cp1252 consoles can't print box-drawing glyphs — degrade gracefully.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except OSError:
        pass

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config, MerchantSearch


# ── Reconciliation core ───────────────────────────────────────────────────

def reconcile(merchants: List[str],
              top_n: int = 3,
              min_show_score: float = 0.0,
              searcher: Optional[MerchantSearch] = None) -> Dict[str, Any]:
    """Run reconciliation for a list of merchant names.

    Returns a dict of DataFrames ready for Excel export:
      {"summary", "matches", "not_found", "all_results", "emails", "contacts"}
    """
    searcher = searcher or MerchantSearch()

    summary_rows: List[Dict[str, Any]] = []
    match_rows: List[Dict[str, Any]] = []
    not_found_rows: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    email_rows: List[Dict[str, Any]] = []
    contact_rows: List[Dict[str, Any]] = []

    for merchant in merchants:
        merchant = (merchant or "").strip()
        if not merchant:
            continue

        results = searcher.search(merchant, limit=top_n, min_score=min_show_score)
        best = results[0] if results else None

        if best and best.overall_score >= config.POSSIBLE_THRESHOLD:
            rec = best.record
            match_rows.append({
                "Merchant (input)": merchant,
                "Best Match": rec.get("merchant_name", ""),
                "Score": round(best.overall_score / 10, 1),
                "Match Type": best.match_type,
                "TID": rec.get("tid", ""),
                "MX Code": rec.get("mxcode", ""),
                "Email": rec.get("email", ""),
                "Phone": rec.get("phone", ""),
                "Contact": rec.get("contact_name", ""),
                "Account Name": rec.get("account_name", ""),
                "Sheet": rec.get("sheet_name", ""),
                "Row": rec.get("row_number", ""),
            })
            # Collect all emails from the matched record
            if rec.get("email"):
                email_rows.append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Email": rec.get("email", ""),
                })
            if rec.get("phone") or rec.get("contact_name") or rec.get("address"):
                contact_rows.append({
                    "Merchant (input)": merchant,
                    "Matched As": rec.get("merchant_name", ""),
                    "Phone": rec.get("phone", ""),
                    "Contact Name": rec.get("contact_name", ""),
                    "Address": rec.get("address", ""),
                })
        else:
            not_found_rows.append({
                "Merchant (input)": merchant,
                "Closest Candidate": (best.record.get("merchant_name", "")
                                      if best else ""),
                "Score": (round(best.overall_score / 10, 1) if best else 0),
            })

        # All results (top-N candidates) for the detail sheet
        for res in results:
            rec = res.record
            all_rows.append({
                "Merchant (input)": merchant,
                "Candidate": rec.get("merchant_name", ""),
                "Score": round(res.overall_score / 10, 1),
                "Match Type": res.match_type,
                "Matched Tokens": ", ".join(res.matched_tokens),
                "TID": rec.get("tid", ""),
                "MX Code": rec.get("mxcode", ""),
                "Email": rec.get("email", ""),
                "Phone": rec.get("phone", ""),
                "Sheet": rec.get("sheet_name", ""),
            })

    # Summary sheet
    n_total = len(match_rows) + len(not_found_rows)
    n_found = len(match_rows)
    summary_rows = [
        {"Metric": "Total merchants", "Value": n_total},
        {"Metric": "Found (>= 50/100)", "Value": n_found},
        {"Metric": "Not found", "Value": len(not_found_rows)},
        {"Metric": "Match rate", "Value": f"{n_found / n_total * 100:.1f}%" if n_total else "—"},
        {"Metric": "Emails recovered", "Value": len(email_rows)},
        {"Metric": "Contacts recovered", "Value": len(contact_rows)},
        {"Metric": "Generated", "Value": datetime.now().strftime("%Y-%m-%d %H:%M")},
    ]

    return {
        "summary": pd.DataFrame(summary_rows),
        "matches": pd.DataFrame(match_rows),
        "not_found": pd.DataFrame(not_found_rows),
        "all_results": pd.DataFrame(all_rows),
        "emails": pd.DataFrame(email_rows),
        "contacts": pd.DataFrame(contact_rows),
    }


def export_report(report: Dict[str, Any], out_path: Path):
    """Write reconciliation report DataFrames to an Excel workbook."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        sheet_order = ["summary", "matches", "not_found", "all_results",
                       "emails", "contacts"]
        pretty_names = {
            "summary": "Summary", "matches": "Matches",
            "not_found": "Not Found", "all_results": "All Results",
            "emails": "Emails", "contacts": "Contacts",
        }
        for key in sheet_order:
            df = report[key]
            if not df.empty:
                df.to_excel(writer, sheet_name=pretty_names[key], index=False)
            else:
                pd.DataFrame({"Note": ["No records"]}).to_excel(
                    writer, sheet_name=pretty_names[key], index=False
                )
    print(f"[OK] Report written to: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────

def _load_merchants(args) -> List[str]:
    merchants: List[str] = []
    if args.list:
        for line in Path(args.list).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "//")):
                merchants.append(line)
    if args.excel:
        # Default to the first sheet when none is specified (sheet_name=None
        # would return a dict of DataFrames and break column access).
        df = pd.read_excel(args.excel, sheet_name=args.sheet) if args.sheet else pd.read_excel(args.excel)
        col = args.col or df.columns[0]
        merchants.extend(str(v).strip() for v in df[col].dropna())
    if args.names:
        merchants.extend(args.names)
    # Deduplicate preserving order
    seen = set()
    unique = []
    for m in merchants:
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Merchant reconciliation report")
    parser.add_argument("names", nargs="*", help="Merchant names to reconcile")
    parser.add_argument("--list", help="Text file with one merchant per line")
    parser.add_argument("--excel", help="Excel file with merchant names")
    parser.add_argument("--sheet", default=None, help="Sheet name in --excel file")
    parser.add_argument("--col", default=None, help="Column name in --excel file")
    parser.add_argument("-o", "--output", default="reports/Merchant_Reconciliation_Report.xlsx",
                        help="Output Excel path")
    parser.add_argument("--top", type=int, default=3, help="Top-N candidates per merchant")
    args = parser.parse_args()

    merchants = _load_merchants(args)
    if not merchants:
        parser.print_help()
        print("\n⚠️  No merchant names provided.")
        return

    print(f"Reconciling {len(merchants)} merchants...")
    report = reconcile(merchants, top_n=args.top)
    export_report(report, args.output)

    # Console summary
    print("\n── Summary ──")
    print(report["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
