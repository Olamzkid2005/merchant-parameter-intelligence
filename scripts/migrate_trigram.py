"""
migrate_trigram.py — Add a trigram FTS5 index to existing merchant databases.

The main FTS table (merchants_fts) uses 'porter unicode61' — great for
whole-word search, but it cannot find partial or typo'd substrings
(e.g. "POWERFOIL" vs "POWERFOIL GLOBAL SERVICES", or "INTERNMATIONAL" vs
"INTERNATIONAL SCHOOL").

The trigram tokenizer indexes every 3-character subsequence, enabling
substring + typo-tolerant matching. This script adds a second FTS table
(merchants_fts_trigram) to an existing database and back-fills it from the
merchants table — no full rebuild required.

The script is schema-adaptive: it only indexes columns that actually exist
in each database (merchant_search.db and merchant_intel.db have different
column layouts).

Run:  python scripts/migrate_trigram.py
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config

# Both databases get the trigram index so search behaves identically.
TARGETS = [
    config.DB_FILE,                                   # merchant_search.db
    config.DB_DIR / "merchant_intel.db",
]

# All columns the search engine may use. Only those present in the target
# database's merchants table are indexed (schema-adaptive).
FTS_COLUMNS = [
    "merchant_name", "slip_header", "alias", "email", "phone", "address",
    "contact_name", "tid", "mxcode", "payable_code", "account_name",
    "merchant_id",
]

# Aliases for columns that differ between the two databases
COLUMN_ALIASES = {
    "phone": ["mobile_phone"],
    "address": ["physical_addr"],
    "tid": ["terminal_id"],
}


def _resolve_columns(cursor) -> tuple:
    """Resolve which FTS columns exist in the merchants table.

    Returns (fts_cols, select_cols):
      - fts_cols:   canonical column names used in the FTS table CREATE
      - select_cols: expressions used in the back-fill SELECT, mapping
                     alias names back to the canonical FTS columns
                     (e.g. "mobile_phone AS phone") so the source table's
                     actual column names are used.
    """
    cursor.execute("PRAGMA table_info(merchants)")
    actual = {r[1] for r in cursor.fetchall()}
    fts_cols, select_cols = [], []
    for col in FTS_COLUMNS:
        if col in actual:
            fts_cols.append(col)
            select_cols.append(col)
        else:
            for alias in COLUMN_ALIASES.get(col, []):
                if alias in actual:
                    fts_cols.append(col)
                    select_cols.append(f"{alias} AS {col}")
                    break
    return fts_cols, select_cols


def migrate(db_path: Path) -> int:
    print(f"\n-- {db_path.name} --")
    if not db_path.exists():
        print(f"  [SKIP] Database not found: {db_path}")
        return 0

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()

    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='merchants'")
    if not c.fetchone():
        print("  [SKIP] No merchants table")
        conn.close()
        return 0

    fts_cols, select_cols = _resolve_columns(c)
    if not fts_cols:
        print("  [SKIP] No indexable columns found")
        conn.close()
        return 0

    print(f"  Indexing columns: {', '.join(fts_cols)}")

    create_sql = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS merchants_fts_trigram USING fts5("
        + ", ".join(fts_cols)
        + ", tokenize='trigram')"
    )
    insert_sql = (
        "INSERT OR IGNORE INTO merchants_fts_trigram(rowid, "
        + ", ".join(fts_cols)
        + ") SELECT id, " + ", ".join(select_cols) + " FROM merchants"
    )

    c.execute(create_sql)
    c.execute(insert_sql)
    conn.commit()

    c.execute("SELECT COUNT(*) FROM merchants_fts_trigram")
    count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM merchants")
    total = c.fetchone()[0]
    print(f"  [OK] Trigram index ready: {count:,}/{total:,} records indexed")

    conn.close()
    return count


if __name__ == "__main__":
    print("Migrating databases to add trigram FTS5 index...")
    for target in TARGETS:
        migrate(target)
    print("\nDone! Trigram search is now available in both databases.")
