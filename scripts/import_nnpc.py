"""
import_nnpc.py — Import NNPC parameter files into merchant_search.db.

Reads all 5 NNPC Excel files, maps their columns to the merchant_search.db
schema, inserts the records with 'NNPC:' source prefix, and rebuilds the 
FTS5 index so the search engine finds them immediately.

Run:  python scripts/import_nnpc.py
"""

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ── NNPC files ─────────────────────────────────────────────────────────────
NNPC_FILES = [
    ("Batch (empty)",       config.DATA_DIR / "NNPC PARAMETER FILE BATCH .xlsx"),
    ("Batch 1",             config.DATA_DIR / "NNPC PARAMETER FILE BATCH 1.xlsx"),
    ("Batch 2",             config.DATA_DIR / "NNPC PARAMETER FILE BATCH 2.xlsx"),
    ("Batch 4",             config.DATA_DIR / "NNPC PARAMETER FILE BATCH 4.xlsx"),
    ("Master",              config.DATA_DIR / "NNpc parameter master.xlsx"),
]

DB_PATH = config.DB_FILE  # merchant_search.db

# ── NNPC-to-DB column mapping ──────────────────────────────────────────────
# Maps NNPC column names (lowercase key) to db schema field names
# Removes excessive escaping from user messages
NNPC_COLUMN_MAP = {
    "merchantname":       "merchant_name",
    "merchantid":         "merchant_id",
    "mxcode":             "mxcode",
    "payable":            "payable_code",
    "alias":              "alias",
    "contacttitle":       "contact_title",
    "contactname":        "contact_name",
    "mobilephone":        "phone",
    "email":              "email",
    "emailalerts":         "email",
    "dealer name":        "contact_name",          # fallback contact
    "physicaladdr":       "address",
    "terminalid":         "tid",
    "terminalmodelcode":  "terminal_type",
    "bankcode":           "bank",
    "bankaccno":          "account_number",
    "bankacctype":         None,  # skip — internal
    "slipheader":         "slip_header",
    "slipfooter":         None,
    "businessoccupationcode": None,
    "merchantcategorycode":   "remarks",
    "statecode":          "state",
    "visaacquireridnumber":   None,
    "verveacquireridnumber":  None,
    "mastercardacquireridnumber": None,
    "terminalownercode":  None,
    "lga/lcda":           "address",               # append to address
    "postcode":           None,
    "merchant url":       None,
    "accountname":        "account_name",
    "ptsp":               "ptsp",
    "device s/n":         "terminal_serial",
    "device serial no.":  "terminal_serial",
}

# Extra columns that only exist in the Master file
MASTER_EXTRA_MAP = {
    "dealer name":        "contact_name",
    "dealer account no":  "account_number",
    "dealer bank name":   "bank",
}


def nnpc_source_tag(file_label: str) -> str:
    """Create a sheet_name value that identifies the NNPC source."""
    return f"NNPC:{file_label}"


def normalize_col(raw: str) -> Optional[str]:
    """Map an NNPC column name to db field name."""
    key = raw.strip().lower()
    key = re.sub(r"\s+", " ", key)
    
    # Primary map
    for pattern, field in NNPC_COLUMN_MAP.items():
        if pattern == key:
            return field
        if key.startswith(pattern):
            return field
        if pattern in key:
            return field
    
    return raw  # fallback — keep as-is (will become a raw_data field)


def clean_val(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    return s.replace("\xa0", " ").replace("\ufffd", "")


def is_real_name(val: str) -> bool:
    if not val:
        return False
    if re.match(r"^[\d.]+$", val):
        return False
    if not re.search(r"[A-Za-z]", val):
        return False
    alpha = sum(1 for c in val if c.isalpha())
    return alpha >= 3


def db_schema_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS merchants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_name        TEXT NOT NULL,
    row_number        INTEGER,
    merchant_name     TEXT,
    merchant_id       TEXT,
    mxcode            TEXT,
    payable_code      TEXT,
    tid               TEXT,
    terminal_serial   TEXT,
    slip_header       TEXT,
    email             TEXT,
    phone             TEXT,
    address           TEXT,
    contact_name      TEXT,
    contact_title     TEXT,
    account_name      TEXT,
    account_number    TEXT,
    bank              TEXT,
    state             TEXT,
    state_code        TEXT,
    bvn               TEXT,
    ptsp              TEXT,
    terminal_type     TEXT,
    deployment_status TEXT,
    alias             TEXT,
    static_acc_no     TEXT,
    remarks           TEXT,
    raw_data          TEXT,
    imported_at       TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS merchants_fts USING fts5(
    merchant_name,
    slip_header,
    alias,
    email,
    phone,
    address,
    contact_name,
    tid,
    mxcode,
    payable_code,
    account_name,
    merchant_id,
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS merchants_fts_trigram USING fts5(
    merchant_name,
    slip_header,
    alias,
    email,
    phone,
    address,
    contact_name,
    tid,
    mxcode,
    payable_code,
    account_name,
    merchant_id,
    tokenize='trigram'
);

CREATE INDEX IF NOT EXISTS idx_merchant_name ON merchants(merchant_name);
CREATE INDEX IF NOT EXISTS idx_tid ON merchants(tid);
CREATE INDEX IF NOT EXISTS idx_email ON merchants(email);
CREATE INDEX IF NOT EXISTS idx_slip_header ON merchants(slip_header);
CREATE INDEX IF NOT EXISTS idx_mxcode ON merchants(mxcode);
"""


def import_nnpc():
    logger.info("=" * 70)
    logger.info("  IMPORTING NNPC DATA INTO merchant_search.db")
    logger.info("=" * 70)

    # Ensure DB exists with schema
    ensure_db = not DB_PATH.exists()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(db_schema_sql())
    conn.commit()
    if ensure_db:
        logger.info("  ✅ Created fresh merchant_search.db schema")
    else:
        logger.info("  ✅ Schema ensured (DB already exists)")

    c = conn.cursor()

    total_inserted = 0
    total_files = 0
    fts_batch: List[Tuple[int, Tuple]] = []
    next_id = _get_next_id(conn)

    for file_label, file_path in NNPC_FILES:
        if not file_path.exists():
            logger.info(f"\n  [SKIP] {file_label:<15} — file not found: {file_path.name}")
            continue

        try:
            xls = pd.ExcelFile(str(file_path))
        except Exception as e:
            logger.info(f"\n  [ERROR] {file_label:<15} — {e}")
            continue

        source_tag = nnpc_source_tag(file_label)
        file_inserted = 0

        for sheet in xls.sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=sheet, dtype=str, keep_default_na=False)
                df = df.dropna(axis=1, how="all")
            except Exception as e:
                logger.info(f"    [ERROR] Sheet {sheet}: {e}")
                continue

            if len(df) < 2:
                continue

            # Build column mapping for this sheet
            col_map = {}
            for raw_col in df.columns:
                db_field = normalize_col(str(raw_col))
                if db_field is not None:
                    col_map[raw_col] = db_field

            sheet_inserted = 0
            for idx, row in df.iterrows():
                rec: Dict[str, Any] = {
                    "sheet_name": source_tag,
                    "row_number": idx + 2,
                }
                raw_parts: Dict[str, str] = {}

                for raw_col, db_field in col_map.items():
                    val = clean_val(row.get(raw_col, ""))
                    # Smart merchant_name: prefer DEALER NAME if MERCHANTNAME is just a code
                    if db_field == "merchant_name" and not is_real_name(val):
                        for dn_col in ["DEALER NAME", "Dealer Name", "dealer name"]:
                            dn_val = clean_val(row.get(dn_col, ""))
                            if is_real_name(dn_val):
                                val = dn_val
                                break
                    rec[db_field] = val
                    raw_parts[str(raw_col)] = val

                rec["raw_data"] = json.dumps(raw_parts, default=str)
                rec["imported_at"] = datetime.now().isoformat()

                row_tuple = (
                    rec.get("sheet_name", source_tag),
                    rec.get("row_number", 0),
                    rec.get("merchant_name", ""),
                    rec.get("merchant_id", ""),
                    rec.get("mxcode", ""),
                    rec.get("payable_code", ""),
                    rec.get("tid", ""),
                    rec.get("terminal_serial", ""),
                    rec.get("slip_header", ""),
                    rec.get("email", ""),
                    rec.get("phone", ""),
                    rec.get("address", ""),
                    rec.get("contact_name", ""),
                    rec.get("contact_title", ""),
                    rec.get("account_name", ""),
                    rec.get("account_number", ""),
                    rec.get("bank", ""),
                    rec.get("state", ""),
                    rec.get("state_code", ""),
                    rec.get("bvn", ""),
                    rec.get("ptsp", ""),
                    rec.get("terminal_type", ""),
                    rec.get("deployment_status", ""),
                    rec.get("alias", ""),
                    rec.get("static_acc_no", ""),
                    rec.get("remarks", ""),
                    rec["raw_data"],
                    rec["imported_at"],
                )

                c.execute("""INSERT INTO merchants (
                    sheet_name, row_number, merchant_name, merchant_id,
                    mxcode, payable_code, tid, terminal_serial,
                    slip_header, email, phone, address,
                    contact_name, contact_title, account_name, account_number,
                    bank, state, state_code, bvn,
                    ptsp, terminal_type, deployment_status, alias,
                    static_acc_no, remarks, raw_data, imported_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row_tuple)
                row_id = c.lastrowid

                fts_batch.append((row_id, (
                    rec.get("merchant_name", ""),
                    rec.get("slip_header", ""),
                    rec.get("alias", ""),
                    rec.get("email", ""),
                    rec.get("phone", ""),
                    rec.get("address", ""),
                    rec.get("contact_name", ""),
                    rec.get("tid", ""),
                    rec.get("mxcode", ""),
                    rec.get("payable_code", ""),
                    rec.get("account_name", ""),
                    rec.get("merchant_id", ""),
                )))

                sheet_inserted += 1
                file_inserted += 1
                total_inserted += 1

                if len(fts_batch) >= config.DB_BATCH_SIZE:
                    _flush_fts(conn, fts_batch)
                    fts_batch = []

            if sheet_inserted > 0:
                logger.info(f"  [{file_label:<15}] Sheet: {sheet:<20} → {sheet_inserted:>4} rows")

        if file_inserted > 0:
            total_files += 1
            logger.info(f"  [{file_label:<15}] Total: {file_inserted} rows")

    # Flush remaining FTS batch
    if fts_batch:
        _flush_fts(conn, fts_batch)

    conn.commit()
    
    # Create indexes if they don't exist
    logger.info("\n  📇 Ensuring indexes...")
    for idx_name in ["idx_mxcode", "idx_merchant_name", "idx_tid", "idx_email", "idx_slip_header"]:
        try:
            col_for_idx = idx_name.replace("idx_", "")
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON merchants({col_for_idx})")
        except:
            pass
    conn.commit()

    logger.info(f"\n  {'=' * 60}")
    logger.info(f"  ✅ NNPC IMPORT COMPLETE")
    logger.info(f"      Files imported: {total_files}")
    logger.info(f"      Rows inserted:  {total_inserted:,}")
    logger.info(f"      Total in DB:    {_get_total_rows(conn):,}")
    logger.info(f"  {'=' * 60}")

    conn.close()


def _get_next_id(conn) -> int:
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM merchants")
    return c.fetchone()[0]


def _get_total_rows(conn) -> int:
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM merchants")
    return c.fetchone()[0]


def _flush_fts(conn, batch: List[Tuple[int, Tuple]]):
    """Bulk insert into FTS5 index."""
    c = conn.cursor()
    for row_id, (mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi) in batch:
        try:
            c.execute(
                """INSERT OR IGNORE INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
            )
            # Also index into the trigram table (substring/typo search)
            c.execute(
                """INSERT OR IGNORE INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
            )
        except sqlite3.OperationalError:
            # Table might not support INSERT OR IGNORE in some FTS5 modes
            try:
                c.execute("DELETE FROM merchants_fts WHERE rowid = ?", (row_id,))
                c.execute(
                    """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                       email, phone, address, contact_name, tid, mxcode, payable_code,
                       account_name, merchant_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_id, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
                )
                c.execute("DELETE FROM merchants_fts_trigram WHERE rowid = ?", (row_id,))
                c.execute(
                    """INSERT INTO merchants_fts_trigram(rowid, merchant_name,
                       slip_header, alias, email, phone, address, contact_name, tid,
                       mxcode, payable_code, account_name, merchant_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_id, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
                )
            except Exception as fts_err:
                logger.warning(f"FTS5 insert failed for row {row_id}: {fts_err}")
    conn.commit()


def verify():
    """Verify the imported NNPC data is searchable."""
    logger.info("\n" + "=" * 70)
    logger.info("  VERIFICATION: Testing NNPC merchant searches")
    logger.info("=" * 70)

    from merchant_intelligence import MerchantSearch

    searcher = MerchantSearch()

    test_queries = [
        "LAGOON WATERS LTD",
        "PETER CHIDI ANUCHA",
        "BIDWILL ENERGY RESOURCES",
        "BARAMA ENERGY",
        "TEEJAY PETROLEUM",
        "FLINTFOL OIL AND GAS",
        "DYNAMIC DRILLING",
    ]

    for query in test_queries:
        logger.info(f"\n  🔍 Query: {query}")
        results = searcher.search(query, limit=3, min_score=0)
        if results:
            for res in results[:3]:
                score = round(res.overall_score / 10, 1)
                name = res.record.get("merchant_name", "")[:55]
                mx = res.record.get("mxcode", "")[:12]
                sheet = res.record.get("sheet_name", "")[:20]
                email = res.record.get("email", "")[:35]
                logger.info(
                    f"    {score:4.1f}/10  {name:55s}"
                    f"\n              MX={mx} sheet={sheet} email={email}"
                )
        else:
            logger.info("    (no results)")


if __name__ == "__main__":
    import_nnpc()
    verify()
    logger.info("\n  ✅ DONE — NNPC data now searchable alongside main parameter data")
