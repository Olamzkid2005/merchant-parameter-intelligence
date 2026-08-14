"""
rebuild_db.py — Full rebuild of merchant_search.db from the Excel workbook.

This script:
1. Reads ALL sheets from the Excel file
2. Intelligently maps columns using config.py's COLUMN_KEYWORDS
3. Rebuilds merchant_search.db with the correct schema + FTS5 index
4. Verifies the previously-missing merchants are now findable

Run:  python scripts/rebuild_db.py
"""

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────

EXCEL_PATH = config.EXCEL_FILE
DB_PATH = config.DB_FILE  # merchant_search.db

# Every known column keyword mapped to the normalized field name.
# The keys match the merchant_search.db schema columns exactly.
COLUMN_MAP_RULES = {
    "account_name": [
        "account name", "account_name", "settlement account",
        "settlement account name", "merchant account name",
        "merchantaccountname", "oldmerchantaccountname", "newmerchantaccountname",
        "oldmerchantAccountName", "newmerchantAccountName",
        # Exact-match priority over the "static acc" starts-with rule, so
        # "Static Account Name" (the bank/account label column in the
        # static_account_terminal reports) routes here instead of colliding
        # with "Static Account Number" → static_acc_no.
        "static account name",
        # Medplus.xlsx and similar static-account exports head the account
        # holder "BENEFICIARY NAME" (e.g. "INTERSWITCH/MEDPLUS 1004"). It is
        # the name on the account — account_name, not a person contact.
        "beneficiary name", "beneficiary",
    ],
    "merchant_name": [
        "merchant name", "merchant_name", "merchantname", "business name",
        "trading name", "outlet", "company name", "organisation",
        "organization", "legal name", "store name", "settlement name",
        "dba", "doing business as", "customer name",
    ],
    "slip_header": [
        "slip header", "slip_header", "slipheader", "dba name",
        "receipt name", "slipheader",
    ],
    "merchant_id": [
        "merchant id", "merchant_id", "merchantid", "mid",
        "merchant code", "merchant number", "merchant no",
    ],
    "mxcode": [
        "mxcode", "mx code", "mx",
    ],
    "payable_code": [
        "payable", "payable code", "payable id", "payableid",
    ],
    # NOTE: no bare "terminal" keyword — it greedily matches TERMINAL OWNER
    # CODE (507) and TERMINAL TYPE (POS) which then overwrite the REAL
    # terminal id in the tid column (38,884 rows polluted with 507). Real
    # TID columns are named "tid" / "terminal id" / "terminal number".
    "tid": [
        "tid", "terminal id", "terminal_id", "terminalid",
        "terminal number", "terminal no",
        # static_account_terminal reports head the real TID column
        # "terminal code" (e.g. 2103O338, 2ISW1410). Safe against
        # TERMINAL OWNER CODE / TERMINAL MODEL CODE — "terminal code"
        # is not a substring of either.
        "terminal code",
    ],
    "terminal_serial": [
        "serial", "terminal serial", "serial number",
    ],
    "email": [
        "email", "e-mail", "mail", "email alerts", "email_alerts",
    ],
    "phone": [
        "phone", "mobile", "telephone", "tel", "phone number",
        "mobile phone", "mobile_phone", "mobilephone",
        "mobile number", "contact phone",
    ],
    "address": [
        "address", "physical addr", "physicaladdr", "physical_addr",
        "merchantphysicaladdr", "merchant address", "terminal address",
        "location", "street", "city", "town", "lga", "local government",
    ],
    "contact_name": [
        "contact name", "contact_name", "contactname",
        "contact person", "contact",
    ],
    "contact_title": [
        "title", "contact title", "contact_title",
    ],
    "account_number": [
        "account no", "account number", "acct no", "acct num",
        "bank account", "settlement account number",
        # Diamond Bank sheet: "Diamond Acc No" (the primary account) and
        # "Access Alternate" (a second account number) both hold 8-10 digit
        # account numbers — map them so they're searchable.
        "diamond acc no", "access alternate",
        # Change-of-details blocks: OLD BANK ACC NO / NEW BANK ACC NO hold
        # the 10-digit account numbers (before/after a change request). The
        # "bank acc no" fragment must out-rank the bare "bank" keyword below
        # so these land in account_number, not bank.
        "bank acc no", "old bank acc no", "new bank acc no",
        "old account no", "new account no",
        # Truncated variants seen in the Change-of-details sheet ("OLD BANK
        # ACC" without the trailing NO). Without these, "bank" wins the
        # substring pass and the 10-digit account lands in the bank column.
        "bank acc", "old bank acc", "new bank acc",
    ],
    "bank": [
        "bank", "bank name", "financial institution", "bank code",
        "bankcode", "bank_code",
        # Change-of-details blocks: OLD BANK CODE / NEW BANK CODE are NIBSS
        # bank codes (e.g. 057 -> NEW). "old bank code" / "new bank code"
        # fragments resolve here.
        "old bank code", "new bank code",
    ],
    "state": [
        "state", "state code", "state_code", "statecode",
    ],
    "state_code": [
        "state code", "state_code", "statecode",
    ],
    "bvn": [
        "bvn", "bank verification number",
    ],
    "ptsp": [
        "ptsp", "ptsp code", "ptspcode", "ptsp_code",
    ],
    "terminal_type": [
        "terminal type", "terminal model", "device type", "device model",
    ],
    "deployment_status": [
        "deployment status", "status", "sim status",
    ],
    "remarks": [
        "remark", "remarks", "reason", "comment", "description", "narrative",
    ],
    "alias": [
        "alias", "ussd alias", "ussd", "short code",
    ],
    "static_acc_no": [
        "static acc", "static account", "staticacc",
    ],
    # When the merchant was onboarded. The main 2ISW sheets head this
    # "MONTH OF REQUEST" (26K+ rows of real dates); Bank Mx code calls it
    # "DATE CREATED". No bare 'request'/'date' keywords — they would grab
    # 'Request Source' / 'DATE GENERATED' / 'Deployment Date'.
    "onboarded_date": [
        "month of request", "date created", "created date",
        "onboard date", "onboarded date", "request date",
        "date of request", "submission date",
    ],
}


# Headers that LOOK like they should map to a field but must NOT be captured
# (they carry type/flag codes, not merchant data). Checked before any keyword
# pass so their values never pollute real fields. Written in the lower-cased,
# space-normalised form produced by _normalize_header (camelCase and known
# concatenations already split).
COLUMN_EXCLUDES = {
    "bank acc type",        # 1 = individual, 2 = corporate — NOT a bank
    "old bank acc type",
    "new bank acc type",
    "account type",         # same type code under a different header
    "client secret",        # API credential — not merchant data
    "client id",            # API credential — not merchant data
    "email alerts",         # Y/N flag column — never an address
    "email alert",
    "fee",                  # PTSP_fee / settlement fees — a rate, not a ptsp
    "ptsp fee",
    "settlement bank fee",
}

# Concatenated/camelCase headers observed across the workbooks (NIBSS FORMAT,
# Sameday, NNPC batches, Bank Mx code, Ifis). Splitting them up front means the
# same keyword rules handle spaced and unspaced headers identically.
_KNOWN_SPLITS = {
    "bankaccno": "bank acc no", "bankaccntno": "bank acc no",
    "bankacctno": "bank acc no", "bankacctnumber": "bank acc no",
    "bankacctype": "bank acc type", "oldbankacctype": "old bank acc type",
    "newbankacctype": "new bank acc type",
    "oldbankaccno": "old bank acc no", "newbankaccno": "new bank acc no",
    "oldbankacctno": "old bank acc no", "newbankacctno": "new bank acc no",
    "oldbankacc": "old bank acc", "newbankacc": "new bank acc",
    "bankacc": "bank acc",
    "oldbankcode": "old bank code", "newbankcode": "new bank code",
    "bankcode": "bank code",
    "oldmerchantaccountname": "old merchant account name",
    "newmerchantaccountname": "new merchant account name",
    "merchantaccountname": "merchant account name",
    "merchantphysicaladdr": "merchant physical addr",
    "oldphysicaladdr": "old physical addr", "newphysicaladdr": "new physical addr",
    "emailalerts": "email alerts", "emailalert": "email alert",
    "mobilephone": "mobile phone", "contactname": "contact name",
    "contacttitle": "contact title", "terminalid": "terminal id",
    "terminalmodelcode": "terminal model code", "terminalownercode": "terminal owner code",
    "terminalcode": "terminal code",
    "terminaltype": "terminal type", "terminalgroupid": "terminal group id",
    "merchantid": "merchant id", "merchantname": "merchant name",
    "merchantcode": "merchant code", "merchantcategorycode": "merchant category code",
    "merchantacquirerid": "merchant acquirer id", "merchantaddresslgacode": "merchant address lga code",
    "terminaladdresslgacode": "terminal address lga code", "terminaladdress": "terminal address",
    "ptspcode": "ptsp code", "ptspfee": "ptsp fee",
    "settlementbankfee": "settlement bank fee", "payablecode": "payable code",
    "payableid": "payable id", "statelga": "state lga", "businessoccupationcode": "business occupation code",
    "visaacquireridnumber": "visa acquirer id number",
    "verveacquireridnumber": "verve acquirer id number",
    "mastercardacquireridnumber": "mastercard acquirer id number",
    "merchantacctdomicilebankcode": "merchant acct domicile bank code",
    "old merchantacctdomicilebankcode": "old merchant acct domicile bank code",
    "newmerchantacctdomicilebankcode": "new merchant acct domicile bank code",
    "terminalmodeldescription": "terminal model description", "appname": "app name",
    "appversion": "app version", "statecode": "state code", "postcode": "post code",
    "device sn": "devicesn", "devicename": "device name", "devicetype": "device type",
    "bank account number": "bank acc no", "bank account": "bank acc no",
    "account number": "bank acc no", "new account number": "new bank acc no",
    "old account number": "old bank acc no",
}


def _normalize_header(raw: str) -> str:
    """Lower-case a raw header, split camelCase, and expand known concatenations
    so spaced and unspaced variants of the same column map identically.

    'oldbankAccNo' → 'old bank acc no', 'bankAccNo' → 'bank acc no',
    'EMAILALERTS' → 'email alerts', 'PTSP_fee' → 'ptsp fee'.
    """
    clean = raw.replace("\xa0", " ").strip().lower()
    clean = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", clean)   # camelCase split
    clean = re.sub(r"[_-]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    # Expand known concatenations (whole token or leading-part match, e.g.
    # "oldbankaccno" → "old bank acc no"; "bankaccno1" → "bank acc no1" not
    # required — headers here are clean tokens).
    for k, v in _KNOWN_SPLITS.items():
        if clean == k:
            return v
        # camelCase leftovers like 'oldbankAccNo' already split to
        # 'oldbank acc no' — fix the leading concatenated part.
        if clean.startswith(k) and len(clean) > len(k) and clean[len(k)] == " ":
            return v + clean[len(k):]
    return clean


def normalize_col_name(raw: str) -> str:
    """Match a raw column name (e.g. 'Merchant Account Name') to a normalized
    field name (e.g. 'account_name') using the keyword rules.

    Priority order:
      0. Excluded columns (BANK ACC TYPE, EMAIL ALERTS…) are returned unmapped
      1. Exact match (clean == kw)          — "merchant id" → merchant_id
      2. Starts-with (clean.startswith(kw))  — "merchant name" → merchant_name
      3. Substring (kw in clean)             — fallback for partial matches
    This prevents greedy keywords like "merchant" from catching "MERCHANT ID"
    before the more specific "merchant id" rule fires.
    """
    clean = _normalize_header(raw)
    if clean in COLUMN_EXCLUDES or any(c in clean for c in (" fee", "fees")):
        return raw

    # Pass 1: Exact match (highest priority)
    for field, keywords in COLUMN_MAP_RULES.items():
        for kw in keywords:
            if clean == kw:
                return field

    # Pass 2: Starts-with match (medium priority)
    for field, keywords in COLUMN_MAP_RULES.items():
        for kw in keywords:
            if clean.startswith(kw):
                return field

    # Pass 3: Substring match (lowest priority — catches "merchant" → merchant_name).
    # Word-boundary anchored so short keywords ("dba", "mx", "mid", "bank")
    # can never match inside an unrelated word ("oldbankcode" must not hit
    # merchant_name via the "dba" fragment).
    for field, keywords in COLUMN_MAP_RULES.items():
        for kw in keywords:
            if len(kw) >= 3 and re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(kw), clean):
                return field

    return raw  # fallback — keep original


def clean_val(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    s = s.replace("\xa0", " ").replace("\ufffd", "")
    return s


def clean_date(val: Any) -> str:
    """Normalise a date-ish cell for the onboarded_date field.

    pandas reads Excel datetime cells as '2021-10-01 00:00:00' (or the
    source exports them that way) — strip the time part so the profile shows
    a clean date. Non-datetime strings are returned as-is.
    """
    v = clean_val(val)
    if not v:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T].*$", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})(?:[ T].*)?$", v)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return v


def _is_real_name(val: str) -> bool:
    """Check if a value looks like a real merchant name (has words, not just codes).
    Returns True for names like 'SWEB_MARYLAND MALL', False for codes like '4789.0'."""
    if not val:
        return False
    # Pure numbers or short codes are not real names
    if re.match(r'^[\d.]+$', val):
        return False  # numeric only
    if len(val) <= 2:
        return False  # too short
    # Must have at least one letter
    if not re.search(r'[A-Za-z]', val):
        return False
    # Must have at least 2 characters that are not just digits/punctuation
    alpha_count = sum(1 for c in val if c.isalpha())
    if alpha_count < 3:
        return False
    return True


# Real TIDs across the workbooks: 2ISW... (2ISW166C), Ifis 2ISB... (2ISBI6K2),
# Bank Mx 2xxx forms (2032ZAX1, 2214E879), 8-digit (21030173), and the
# 2103O338 form. The 16-char MERCHANT IDs (2ISW123456LA455) never match {5,8}.
# Single source of truth — also referenced by build_intelligence_db.py's
# headerless-sheet alignment scorer.
TID_PATTERN = re.compile(
    r"^(?:2IS[A-Z0-9]{5,8}|2\d{3}[A-Z0-9]{4}|\d{8}|\d{4}[A-Z]\d{3})$",
    re.I)


def _is_real_tid(val: str) -> bool:
    """Check if a value looks like a real terminal ID.

    Real TIDs in these workbooks are 8-char (2ISW166C), 8-digit (21030173)
    or the 2103O338 form. Owner codes (507), terminal types (POS), GPRS and
    other leaked values are NOT real TIDs.
    """
    return bool(TID_PATTERN.match((val or "").strip()))


# Some workbook cells contain a pasted DataFrame printout instead of a
# plain value. Every observed leak is multi-line; no legit single-cell value
# in these columns ever contains a newline, so '\n' is a safe trigger:
#
#   'terminalId    2ISW2587\nterminalId    2ISW2587\nName: 263, dtype: str'
#   'TERMINAL ID    \nTERMINAL ID    \nName: 3929, dtype: str'        (no TID)
#   'NEW MERCHANT ACCOUNT NAME    MAGGII BEAUTY PALACE\n...\nName: N, dtype: str'
#   'NEW BANK ACC NO           232\nNEW BANK ACC NO    0067289285\nName: N...'
#
# The last 'Name: <n>, dtype: str' line is the pandas footer; the remaining
# lines each carry 'LABEL    value'. The meaningful value is the LONGEST
# non-empty one (leak lines are often truncated at different widths — e.g.
# '232' vs '0067289285', 'ERIC KAYSER' vs 'ERIC K').
_PANDAS_NAME_FOOTER = re.compile(r"^Name:\s*\d+,\s*dtype:\s*str\s*$")


def _clean_pandas_leak(val: str) -> str:
    """Return the meaningful content of a pandas-printout leak cell.

    Single-line values pass through unchanged. Multi-line values that are
    printout fragments get their longest non-empty value extracted ('' when
    the fragment carried no value at all).
    """
    v = (val or "").strip()
    if "\n" not in v:
        return v
    lines = v.splitlines()
    if lines and _PANDAS_NAME_FOOTER.match(lines[-1].strip()):
        lines = lines[:-1]
    values = []
    for ln in lines:
        if not ln.strip():
            continue
        # Split on the RAW line (trailing label spaces are the separator) —
        # stripping first would eat them and leave the bare label behind.
        parts = re.split(r"\s{2,}", ln, maxsplit=1)
        piece = parts[1].strip() if len(parts) > 1 else ln.strip()
        if piece:
            values.append(piece)
    if not values:
        return ""
    return max(values, key=len)


# Fields that make a row worth keeping — a row where EVERY one of these
# cleaned to empty is a phantom artifact (a leaked cell spilled into extra
# rows by the multi-block decoder) and is dropped at build time.
_ROW_CONTENT_FIELDS = (
    "merchant_name", "merchant_id", "mxcode", "payable_code", "tid",
    "terminal_serial", "slip_header", "email", "phone", "address",
    "contact_name", "account_name", "account_number", "alias",
    "bank", "state", "onboarded_date",
)


def _row_has_content(rec: Dict[str, Any]) -> bool:
    """True when the cleaned record carries at least one meaningful value."""
    return any(str(rec.get(f) or "").strip() for f in _ROW_CONTENT_FIELDS)


# ── Stacked multi-block sheet decoding (shared by both build scripts) ─────
# Some sheets (Change of merchant details, Deactivated TID) stack many export
# blocks vertically — each block repeats its own header row and the layouts
# differ between blocks (some add MXCODE/PAYABLE CODE/CLIENT ID, some use
# OLD/NEW BANK CODE, some OLD/NEW PHYSICAL ADDR, some NEW ALIAS…). The single
# header-row read (header=row 0) ingests every later header as a data row and
# misaligns every block whose layout differs from the first. These helpers find
# EVERY header row and decode each block against its own header.


def detect_all_header_rows(raw_df) -> List[int]:
    """Find every header row in a stacked multi-block sheet.

    Same label-ratio test as a single header detector, but run over the WHOLE
    sheet. A cheap first-cell filter keeps the scan fast even on 38K-row
    sheets. Block headers vary: most start with 'MERCHANT NAME', but the
    Change sheet also stacks blocks starting with 'OLD/NEW MERCHANT NAME',
    'MONTH OF REQUEST' (report title) or an NBSP-prefixed camelCase
    'merchantName' — all normalise to contain 'merchant name' except the
    report title, so the filter accepts both and also checks the first 4
    cells for headers that start with 'MERCHANT ID'.
    """
    headers: List[int] = []
    for i in range(len(raw_df)):
        row_cells = raw_df.iloc[i].tolist()
        first = clean_val(row_cells[0])
        first_norm = _normalize_header(first)
        # Cheap first-cell filter. Most block headers start with 'MERCHANT
        # NAME', but the Change sheet also stacks fragments whose first cell
        # is 'OLD/NEW MERCHANT NAME' (a second block type), 'MONTH OF
        # REQUEST' (report title), or an NBSP-prefixed camelCase
        # '\xa0\xa0merchantName'. Clean/normalise first so all these forms
        # count ('merchant name' is a substring of every variant), and also
        # accept a header whose first 4 cells contain the 'merchant name'
        # label at another position (blocks that start with 'MERCHANT ID').
        is_candidate = 'merchant name' in first_norm or first_norm == "month of request"
        if not is_candidate:
            for cell in row_cells[:4]:
                if 'merchant name' in _normalize_header(clean_val(cell)):
                    is_candidate = True
                    break
        if not is_candidate:
            continue
        non_empty = 0
        score = 0
        for cell in row_cells:
            v = clean_val(cell)
            if not v:
                continue
            non_empty += 1
            if normalize_col_name(v) != v:
                score += 1
        if non_empty == 0:
            continue
        ratio = score / non_empty
        # A row whose FIRST cell normalises to 'MERCHANT NAME' (or 'MONTH OF
        # REQUEST') is already a strong header signal (data rows start with
        # real merchant names). The ratio only guards against near-empty
        # look-alikes, so keep the bar low enough to catch layout variants
        # that score ~0.48 (e.g. a block header with CONTACT + CONTACT NAME
        # instead of CONTACT TITLE).
        if score >= 2 and ratio >= 0.4:
            headers.append(i)
    return headers


def read_multiblock(raw_df, header_rows: List[int]) -> pd.DataFrame:
    """Decode a stacked multi-block sheet: split at every header row and decode
    each block against ITS OWN header (layouts differ between blocks).

    Returns a DataFrame whose columns are the union of all block headers;
    every block's data rows are mapped through their block's own header row.
    The original raw row indices are preserved as the DataFrame index so
    workbook row numbers stay accurate (caller passes header_offset=-1).
    Fully-blank separator rows are dropped.
    """
    all_cols: List[str] = []
    col_seen = set()
    frames: List[Tuple[pd.DataFrame, int]] = []  # (block_df, first_raw_row)

    for k, h in enumerate(header_rows):
        end = header_rows[k + 1] if k + 1 < len(header_rows) else len(raw_df)
        block_raw = raw_df.iloc[h + 1:end]
        if block_raw.empty:
            continue
        # Blank header cells -> position-based synthetic names (col_7) so the
        # union of all blocks stays small. col_{k}_{j} would mint a UNIQUE
        # column per block (290 blocks x blanks = 2100+ columns) and slow the
        # row-assembly loop from ~1s to ~60s.
        header_cells = [
            (v if (v := clean_val(c)) else f"col_{j}")
            for j, c in enumerate(raw_df.iloc[h].tolist())
        ]
        for c in header_cells:
            if c not in col_seen:
                col_seen.add(c)
                all_cols.append(c)
        block = block_raw.copy()
        block.columns = header_cells
        frames.append((block, h + 1))

    if not frames:
        return pd.DataFrame()

    rows_list: List[List[str]] = []
    index_list: List[int] = []
    for block, first_row in frames:
        for raw_idx, row in block.iterrows():
            out = [str(row.get(c, "")) if c in block.columns else ""
                   for c in all_cols]
            if not any(str(v).strip() for v in out):
                continue
            rows_list.append(out)
            index_list.append(int(raw_idx))

    return pd.DataFrame(rows_list, columns=all_cols, index=index_list)


# ─── Schema ────────────────────────────────────────────────────────────────

DB_SCHEMA_SQL = """
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
    imported_at       TEXT,
    onboarded_date    TEXT
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

CREATE TABLE IF NOT EXISTS aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_name   TEXT NOT NULL,
    alias           TEXT NOT NULL,
    source          TEXT DEFAULT 'auto',
    UNIQUE(merchant_name, alias)
);

CREATE TABLE IF NOT EXISTS learned_mappings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alias           TEXT UNIQUE NOT NULL,
    canonical_name  TEXT NOT NULL,
    confidence      REAL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS search_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT NOT NULL,
    result_merchant TEXT,
    result_score    REAL,
    clicked         INTEGER
);

CREATE TABLE IF NOT EXISTS name_buckets (
    bucket_key TEXT PRIMARY KEY,
    ids        TEXT
);
"""


def build_merchant_search_db():
    """Read the Excel file and build a fresh merchant_search.db.

    Returns True on success, False if the build could not complete
    (workbook missing) so callers can surface a non-zero exit code.
    """

    logger.info("=" * 70)
    logger.info("  REBUILDING merchant_search.db")
    logger.info("=" * 70)

    if not EXCEL_PATH.exists():
        logger.error(f"  ❌ Excel file not found: {EXCEL_PATH}")
        return False

    # Open workbook
    xls = pd.ExcelFile(str(EXCEL_PATH))
    sheet_names = xls.sheet_names
    logger.info(f"\n  Workbook: {EXCEL_PATH.name}")
    logger.info(f"  Sheets: {len(sheet_names)} — {', '.join(sheet_names)}\n")

    # Delete old DB
    if DB_PATH.exists():
        DB_PATH.unlink()
        logger.info("  🗑️  Deleted old merchant_search.db\n")

    # Create fresh DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DB_SCHEMA_SQL)
    conn.commit()
    logger.info("  ✅ Created fresh merchant_search.db schema\n")

    # Process each sheet
    total_rows = 0
    fts_rows_data: List[Tuple] = []
    # Each entry in batch: (row_id, tuple_of_values)
    batch: List[Tuple[int, Tuple]] = []
    next_id = 1  # autoincrement counter

    for sheet_name in sheet_names:
        logger.info(f"  📄 Sheet: {sheet_name}")

        # Stacked multi-block sheets (Change of merchant details, Deactivated
        # TID) decode every block against its own header; otherwise the later
        # headers leak in as data rows and every differing block misaligns.
        raw = pd.read_excel(xls, sheet_name=sheet_name, dtype=str,
                            keep_default_na=False, header=None)
        raw = raw.dropna(axis=1, how="all")
        mb_headers = detect_all_header_rows(raw)
        if len(mb_headers) >= 2:
            mb = read_multiblock(raw, mb_headers)
            if not mb.empty:
                df = mb
                header_offset = -1
            else:
                df = raw.iloc[1:].copy()
                df.columns = [str(c).strip() if str(c).strip() else f"col_{i}"
                              for i, c in enumerate(raw.iloc[0].tolist())]
                header_offset = 0
        else:
            if raw.empty:
                logger.info(f"     (empty, skipped)")
                continue
            hdr_row = 0
            df = raw.iloc[hdr_row + 1:].copy()
            df.columns = [str(c).strip() if str(c).strip() else f"col_{i}"
                          for i, c in enumerate(raw.iloc[hdr_row].tolist())]
            header_offset = hdr_row
        df = df.dropna(axis=1, how="all")

        if df.empty:
            logger.info(f"     (empty, skipped)")
            continue

        # Map columns
        col_mapping: Dict[str, str] = {}
        for raw_col in df.columns:
            norm = normalize_col_name(str(raw_col))
            col_mapping[raw_col] = norm

        # Log discovered mappings
        logger.info(f"     Columns → {', '.join(f'{c}={n}' for c, n in col_mapping.items() if c != n)}")

        sheet_rows = 0
        for idx, row in df.iterrows():
            rec: Dict[str, Any] = {
                "sheet_name": sheet_name,
                "row_number": header_offset + idx + 2,
            }
            raw_parts: Dict[str, str] = {}
            for raw_col, norm in col_mapping.items():
                val = _clean_pandas_leak(clean_val(row.get(raw_col, "")))
                # Smart overwrite: for merchant_name, don't let metadata columns
                # (category codes, LGA codes, acquirer IDs) overwrite real names.
                if norm == "merchant_name" and _is_real_name(rec.get("merchant_name", "")):
                    # Only overwrite a real name if the new value is ALSO a real name
                    # (i.e. prefer the first real name seen, not the last code)
                    if not _is_real_name(val):
                        raw_parts[str(raw_col)] = val
                        continue  # skip code/meta overwrites
                # Smart overwrite: for email, never let a non-address value
                # (e.g. the EMAIL ALERTS Y/N flag column, which maps to the
                # same field) clobber a real address already captured. Only an
                # '@' value may replace an existing email.
                if norm == "email" and "@" in rec.get("email", "") and "@" not in val:
                    raw_parts[str(raw_col)] = val
                    continue
                # Smart overwrite: for tid, never let a non-TID value (e.g.
                # TERMINAL OWNER CODE=507, TERMINAL TYPE=POS) clobber a real
                # terminal id already captured. Only a TID-shaped value may
                # replace the current one.
                if norm == "tid" and rec.get("tid") and not _is_real_tid(val):
                    raw_parts[str(raw_col)] = val
                    continue
                # General guard (multi-block sheets): several raw columns can
                # map to the same field (BANK CODE + BANK, OLD/NEW BANK ACC
                # NO, MERCHANT ACCOUNT NAME + OLD/NEW variants). The union of
                # ALL blocks' headers means a column that is EMPTY in this
                # row (from a different block's layout) would clobber a real
                # value via last-write-wins — never let an empty value
                # overwrite a non-empty one.
                if norm in rec and rec[norm] and not val:
                    raw_parts[str(raw_col)] = val
                    continue
                rec[norm] = clean_date(val) if norm == "onboarded_date" else val
                raw_parts[str(raw_col)] = val

            # Drop rows that are pure pandas-printout artifacts — every field
            # cleaned to empty (phantom rows from the multi-block decoder).
            if not _row_has_content(rec):
                continue

            # Final email shape guard: only an '@' value is a real address.
            # Some source files label a bank/PTSP-name column as EMAIL (e.g.
            # NNpc master rows hold 'Moniepoint'/'Access Bank') and stash the
            # real address elsewhere — drop any non-address leftover so the
            # email field never carries bank names.
            if rec.get("email") and "@" not in rec["email"]:
                rec["email"] = ""

            rec["raw_data"] = json.dumps(raw_parts, default=str)
            rec["imported_at"] = datetime.now().isoformat()

            row_tuple = (
                sheet_name, idx + 2,
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
                rec.get("onboarded_date", ""),
            )

            batch.append((next_id, row_tuple))
            next_id += 1
            sheet_rows += 1

            # Batch insert
            if len(batch) >= config.DB_BATCH_SIZE:
                total_rows += _flush_batch(conn, batch, fts_rows_data)
                batch = []

        logger.info(f"     → {sheet_rows} rows")

    # Flush remaining
    if batch:
        total_rows += _flush_batch(conn, batch, fts_rows_data)

    logger.info(f"\n  📊 Total rows inserted: {total_rows:,}")

    # Rebuild FTS index (insert FTS data we collected)
    logger.info("\n  🔍 Rebuilding FTS5 index...")
    fts_c = conn.cursor()
    fts_insert_count = 0
    trigram_insert_count = 0
    for row_num, (mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi) in fts_rows_data:
        try:
            fts_c.execute(
                """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_num, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
            )
            fts_insert_count += 1
        except sqlite3.IntegrityError:
            pass
        # Also populate the trigram index (substring/typo-tolerant search)
        try:
            fts_c.execute(
                """INSERT OR IGNORE INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_num, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi)
            )
            trigram_insert_count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    logger.info(f"  ✅ FTS5 index: {fts_insert_count:,} entries")
    logger.info(f"  ✅ Trigram index: {trigram_insert_count:,} entries")

    # Create indexes
    logger.info("\n  📇 Creating indexes...")
    c = conn.cursor()
    c.execute("CREATE INDEX IF NOT EXISTS idx_merchant_name ON merchants(merchant_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tid ON merchants(tid)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_email ON merchants(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_slip_header ON merchants(slip_header)")
    conn.commit()
    logger.info("  ✅ Indexes created")

    # ── Post-processing: Fix numeric merchant_names using slip_header ────
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Repairing numeric merchant_names")
    logger.info("=" * 70)
    _repair_code_names(conn)

    # ── Normalized name buckets (instant exact-normalized lookup) ────────
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Building normalized name buckets")
    logger.info("=" * 70)
    from merchant_intelligence.database import build_name_buckets
    n_keys = build_name_buckets(conn)
    logger.info(f"  ✅ name_buckets built with {n_keys:,} keys")

    # Build-time enrichment: per-record quality scores + per-terminal
    # timeline (merchant_events) — powers the Profile Timeline tab and the
    # data-quality signals across the app.
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Quality scores + terminal timeline")
    logger.info("=" * 70)
    from merchant_intelligence.enrich import enrich_database
    enriched = enrich_database(conn)
    logger.info(
        f"  ✅ enrichment done — {enriched['quality_rows']:,} rows scored, "
        f"{enriched['events']:,} timeline events")

    conn.close()
    logger.info(f"\n  ✅ merchant_search.db rebuilt — {total_rows:,} total records\n")
    return True


def _flush_batch(conn, batch_with_ids, fts_rows_data):
    """Insert a batch of records and collect FTS data.

    batch_with_ids: list of (row_id, tuple_of_values)
    """
    c = conn.cursor()
    for row_id, row in batch_with_ids:
        c.execute("""
            INSERT INTO merchants (
                sheet_name, row_number, merchant_name, merchant_id,
                mxcode, payable_code, tid, terminal_serial,
                slip_header, email, phone, address,
                contact_name, contact_title, account_name, account_number,
                bank, state, state_code, bvn,
                ptsp, terminal_type, deployment_status, alias,
                static_acc_no, remarks, raw_data, imported_at, onboarded_date
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)

        # Collect FTS data for this row
        # Tuple indices: 0=sheet_name,1=row_number,2=merchant_name,3=merchant_id,
        # 4=mxcode,5=payable_code,6=tid,7=terminal_serial,
        # 8=slip_header,9=email,10=phone,11=address,
        # 12=contact_name,13=contact_title,14=account_name,15=account_number,
        # 16=bank,17=state,18=state_code,19=bvn,
        # 20=ptsp,21=terminal_type,22=deployment_status,23=alias,
        # 24=static_acc_no,25=remarks,26=raw_data,27=imported_at
        fts_data = (
            row[2],  # merchant_name
            row[8],  # slip_header
            row[23], # alias
            row[9],  # email
            row[10], # phone
            row[11], # address
            row[12], # contact_name
            row[6],  # tid
            row[4],  # mxcode
            row[5],  # payable_code
            row[14], # account_name
            row[3],  # merchant_id
        )
        fts_rows_data.append((row_id, fts_data))

    conn.commit()
    return len(batch_with_ids)


def _repair_code_names(conn):
    """
    Post-processing: copy slip_header to merchant_name wherever merchant_name
    is a numeric code (e.g. "4789.0", "5411.0") but slip_header has a real
    merchant name.

    Also updates the FTS5 index so these fixes are searchable immediately.
    """
    c = conn.cursor()

    # 1. Count how many records have numeric merchant_name but real slip_header
    c.execute("""
        SELECT COUNT(*) FROM merchants
        WHERE slip_header != ''
          AND slip_header IS NOT NULL
          AND slip_header GLOB '*[A-Za-z]*'
          AND (
               merchant_name GLOB '*[0-9]*'
           AND merchant_name NOT GLOB '*[A-Za-z]*'
          )
    """)
    fixable = c.fetchone()[0]
    logger.info(f"\n  🔧 Records with numeric merchant_name + real slip_header: {fixable:,}")

    if fixable == 0:
        logger.info("  ✅ Nothing to repair")
        return

    # 2. Capture affected rows BEFORE the UPDATE (critical — the WHERE conditions
    #    will no longer match after merchant_name is changed)
    c.execute("""
        SELECT id, merchant_name, slip_header, alias, email, phone, address,
               contact_name, tid, mxcode, payable_code, account_name, merchant_id
        FROM merchants
        WHERE slip_header != ''
          AND slip_header GLOB '*[A-Za-z]*'
          AND (merchant_name GLOB '*[0-9]*' AND merchant_name NOT GLOB '*[A-Za-z]*')
    """)
    fixed_rows = c.fetchall()

    # Show examples before fix
    logger.info("  Examples (before fix):")
    for row in fixed_rows[:5]:
        logger.info(f"    id={row[0]}  name=\"{row[1]}\"  slip=\"{row[2]}\"")

    # 3. Perform the update — copy slip_header to merchant_name
    c.execute("""
        UPDATE merchants
        SET merchant_name = slip_header
        WHERE slip_header != ''
          AND slip_header GLOB '*[A-Za-z]*'
          AND (merchant_name GLOB '*[0-9]*' AND merchant_name NOT GLOB '*[A-Za-z]*')
    """)
    updated = c.rowcount
    conn.commit()
    logger.info(f"  ✅ Fixed {updated:,} records — copied slip_header → merchant_name")

    # 4. Update the FTS5 indexes using the captured rows (before they were changed)
    logger.info("\n  🔍 Updating FTS5 indexes for fixed rows...")
    fts_updated = 0
    for row in fixed_rows:
        row_id, _, slip, alias, email, phone, address, contact_name, tid, mxcode, \
            payable_code, account_name, merchant_id = row
        try:
            conn.execute(
                """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rowid) DO UPDATE SET
                       merchant_name=excluded.merchant_name""",
                (row_id, slip, slip, alias, email, phone, address, contact_name,
                 tid, mxcode, payable_code, account_name, merchant_id)
            )
            # Keep the trigram index in sync too
            conn.execute("DELETE FROM merchants_fts_trigram WHERE rowid = ?", (row_id,))
            conn.execute(
                """INSERT INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, slip, slip, alias, email, phone, address, contact_name,
                 tid, mxcode, payable_code, account_name, merchant_id)
            )
            fts_updated += 1
        except sqlite3.OperationalError:
            conn.execute("DELETE FROM merchants_fts WHERE rowid = ?", (row_id,))
            conn.execute(
                """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, slip, slip, alias, email, phone, address, contact_name,
                 tid, mxcode, payable_code, account_name, merchant_id)
            )
            conn.execute("DELETE FROM merchants_fts_trigram WHERE rowid = ?", (row_id,))
            conn.execute(
                """INSERT INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, slip, slip, alias, email, phone, address, contact_name,
                 tid, mxcode, payable_code, account_name, merchant_id)
            )
            fts_updated += 1

    conn.commit()
    logger.info(f"  ✅ FTS5 index updated for {fts_updated:,} rows")

    # 5. Show examples after fix (query by the captured IDs)
    if fixed_rows:
        ids = tuple(r[0] for r in fixed_rows[:3])
        placeholders = ",".join("?" for _ in ids)
        c.execute(f"SELECT id, merchant_name, slip_header FROM merchants WHERE id IN ({placeholders})", ids)
        logger.info("\n  Examples (after fix):")
        for row in c.fetchall():
            logger.info(f"    id={row[0]}  name=\"{row[1]}\"  slip=\"{row[2]}\"")

    # 6. Also update account_name from slip_header for records where account_name
    #    is numeric but slip_header has a real name (e.g. MONEYTRUST rows)
    c.execute("""
        SELECT COUNT(*) FROM merchants
        WHERE slip_header != ''
          AND slip_header GLOB '*[A-Za-z]*'
          AND (account_name GLOB '*[0-9]*' AND account_name NOT GLOB '*[A-Za-z]*')
    """)
    fixable_acct = c.fetchone()[0]
    if fixable_acct > 0:
        logger.info(f"\n  🔧 Also fixing {fixable_acct:,} numeric account_names from slip_header...")
        c.execute("""
            UPDATE merchants
            SET account_name = slip_header
            WHERE slip_header != ''
              AND slip_header GLOB '*[A-Za-z]*'
              AND (account_name GLOB '*[0-9]*' AND account_name NOT GLOB '*[A-Za-z]*')
        """)
        conn.commit()
        logger.info(f"  ✅ Fixed {c.rowcount:,} account_names")


def verify_search():
    """Verify the newly built DB finds the 4 previously-missing merchants."""
    logger.info("=" * 70)
    logger.info("  VERIFICATION: Testing 4 missing merchants")
    logger.info("=" * 70)

    from merchant_intelligence import MerchantSearch

    searcher = MerchantSearch()

    test_cases = [
        "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
        "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
        "MONEYTRUST MICROFINANACE BANK LTD",
        "NWANERI VICTOR",
    ]

    for query in test_cases:
        logger.info(f"\n  🔍 Query: {query}")
        results = searcher.search(query, limit=5, min_score=0)
        if results:
            for res in results[:5]:
                score = round(res.overall_score / 10, 1)
                name = res.record.get("merchant_name", "")[:50]
                slip = res.record.get("slip_header", "")[:30]
                tid = res.record.get("tid", "")
                contact = res.record.get("contact_name", "")[:30]
                email = res.record.get("email", "")[:30]
                acct = res.record.get("account_name", "")[:30]
                mt = res.matched_tokens
                logger.info(
                    f"    {score:4.1f}/10  {name:50s}"
                    f"\n              slip={slip} tid={tid} contact={contact}"
                    f"\n              email={email} acct={acct} tokens={mt}"
                )
        else:
            logger.info("    (no results)")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ok = build_merchant_search_db()
    if ok:
        verify_search()
        logger.info("\n" + "=" * 70)
        logger.info("  ✅ REBUILD COMPLETE")
        logger.info("=" * 70)
    sys.exit(0 if ok else 1)
