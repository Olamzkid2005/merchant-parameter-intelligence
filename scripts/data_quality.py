"""
data_quality.py — Data quality scan of the merchant database.

Reports:
  - Record counts per source sheet
  - Merchants missing emails / phones / contacts / addresses
  - Numeric-code merchant names (likely missing real names)
  - Duplicate TIDs (one TID on multiple records)
  - MX codes mapped to multiple different merchant names
  - Orphan records (no email, phone, MX, TID, or merchant name)

Usage:
  python data_quality.py            # print report
  python data_quality.py -o q.xlsx  # also export Excel
"""

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config


def run_quality(db_path=None) -> Dict[str, Any]:
    """Scan the database and return a structured quality report."""
    db_path = db_path or config.active_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]

    # Per-sheet counts
    sheet_counts = {
        r["sheet_name"]: r["n"]
        for r in c.execute(
            "SELECT sheet_name, COUNT(*) n FROM merchants GROUP BY sheet_name ORDER BY n DESC"
        ).fetchall()
    }

    def missing(field: str) -> int:
        return c.execute(
            f"SELECT COUNT(*) FROM merchants WHERE {field} IS NULL OR {field} = ''"
        ).fetchone()[0]

    # Numeric-code merchant names (no letters) — excludes empty names
    code_names = c.execute(
        "SELECT COUNT(*) FROM merchants WHERE merchant_name != '' "
        "AND merchant_name NOT GLOB '*[A-Za-z]*'"
    ).fetchone()[0]

    # Duplicate TIDs: same TID on >1 record with different merchant names
    dup_tid_rows = []
    for r in c.execute(
        "SELECT tid, COUNT(*) n, COUNT(DISTINCT merchant_name) distinct_names "
        "FROM merchants WHERE tid != '' GROUP BY tid HAVING n > 1 ORDER BY n DESC LIMIT 100"
    ).fetchall():
        dup_tid_rows.append({
            "TID": r["tid"],
            "Records": r["n"],
            "Distinct Names": r["distinct_names"],
        })

    # MX codes mapped to multiple merchant names
    mx_multiname_rows = []
    for r in c.execute(
        "SELECT mxcode, COUNT(DISTINCT merchant_name) distinct_names, "
        "COUNT(*) n FROM merchants WHERE mxcode != '' "
        "GROUP BY mxcode HAVING distinct_names > 1 ORDER BY n DESC LIMIT 100"
    ).fetchall():
        mx_multiname_rows.append({
            "MX Code": r["mxcode"],
            "Distinct Names": r["distinct_names"],
            "Records": r["n"],
        })

    # Orphans: no name, no email, no phone, no mx, no tid
    orphans = c.execute(
        "SELECT COUNT(*) FROM merchants WHERE "
        "(merchant_name IS NULL OR merchant_name = '') AND "
        "(email IS NULL OR email = '') AND "
        "(phone IS NULL OR phone = '') AND "
        "(mxcode IS NULL OR mxcode = '') AND "
        "(tid IS NULL OR tid = '')"
    ).fetchone()[0]

    report = {
        "total": total,
        "sheets": sheet_counts,
        "missing": {
            "email": missing("email"),
            "phone": missing("phone"),
            "contact_name": missing("contact_name"),
            "address": missing("address"),
            "account_name": missing("account_name"),
            "tid": missing("tid"),
            "mxcode": missing("mxcode"),
        },
        "code_names": code_names,
        "duplicate_tids": pd.DataFrame(dup_tid_rows),
        "mx_multiname": pd.DataFrame(mx_multiname_rows),
        "orphans": orphans,
    }
    conn.close()
    return report


def print_report(q: Dict[str, Any]):
    total = q["total"]
    print("=" * 70)
    print("  DATA QUALITY REPORT")
    print("=" * 70)
    print(f"\nTotal records: {total:,}")
    print(f"Numeric-code merchant names: {q['code_names']:,}")
    print(f"Orphan records (no contact info at all): {q['orphans']:,}")

    print("\n-- Missing fields --")
    for field, count in q["missing"].items():
        pct = count / total * 100 if total else 0
        print(f"  {field:<15} {count:>8,}  ({pct:.1f}%)")

    print("\n-- Records per sheet --")
    for sheet, n in q["sheets"].items():
        print(f"  {str(sheet)[:50]:<50} {n:>8,}")

    print(f"\n-- Duplicate TIDs ({len(q['duplicate_tids'])}) --")
    if not q["duplicate_tids"].empty:
        print(q["duplicate_tids"].head(15).to_string(index=False))

    print(f"\n-- MX codes with multiple names ({len(q['mx_multiname'])}) --")
    if not q["mx_multiname"].empty:
        print(q["mx_multiname"].head(15).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="Merchant data quality scan")
    parser.add_argument("-o", "--output", help="Optional Excel export path")
    args = parser.parse_args()

    q = run_quality()
    print_report(q)

    if args.output:
        with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
            pd.DataFrame({
                "Metric": ["Total records", "Code names", "Orphans"]
                + [f"Missing {f}" for f in q["missing"]],
                "Value": [q["total"], q["code_names"], q["orphans"]]
                + [q["missing"][f] for f in q["missing"]],
            }).to_excel(writer, sheet_name="Summary", index=False)
            q["duplicate_tids"].to_excel(writer, sheet_name="Duplicate TIDs", index=False)
            q["mx_multiname"].to_excel(writer, sheet_name="MX Multi-Name", index=False)
            pd.DataFrame(
                [{"Sheet": s, "Records": n} for s, n in q["sheets"].items()]
            ).to_excel(writer, sheet_name="Sheets", index=False)
        print(f"\n[OK] Quality report exported: {args.output}")


if __name__ == "__main__":
    main()
