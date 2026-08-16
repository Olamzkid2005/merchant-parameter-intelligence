"""
build_intelligence_db.py — Turn a whole folder of Excel files into ONE database.

Scans a folder (recursively) for every .xlsx / .xls file, reads ALL sheets
from each, auto-detects the merchant columns (using the same keyword engine
as rebuild_db.py), and writes a single SQLite database — intelligence.db —
with the full schema plus FTS5 and trigram indexes.

The app loads intelligence.db automatically the moment it exists
(see merchant_intelligence/config.active_db()), so after building you can
just start the API and search across ALL parameter files together.

Usage:
    python scripts/build_intelligence_db.py                          # scans data/
    python scripts/build_intelligence_db.py --folder <path>          # any folder
    python scripts/build_intelligence_db.py --out data/intelligence.db
    python scripts/build_intelligence_db.py --watch                  # rebuild on every change
    python scripts/build_intelligence_db.py --watch --interval 3     # poll every 3s

Run:  python scripts/build_intelligence_db.py
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence import config

# Nigerian state names (and safe short codes) used to sniff a headerless
# trailing column that holds state values (e.g. Medplus.xlsx's blank-headed
# LAGOS/ABIA column). Full names always, short codes only when unambiguous
# (LA = Lagos, FCT = Abuja) — codes like "RI" collide with other data.
_NG_STATE_NAMES = {
    "ABIA", "ADAMAWA", "AKWA IBOM", "AKWAIBOM", "ANAMBRA", "BAUCHI",
    "BAYELSA", "BENUE", "BORNO", "CROSS RIVER", "CROSSRIVER", "DELTA",
    "EBONYI", "EDO", "EKITI", "ENUGU", "FCT", "GOMBE", "IMO",
    "JIGAWA", "KADUNA", "KANO", "KATSINA", "KEBBI", "KOGI", "KWARA",
    "LAGOS", "NASARAWA", "NIGER", "OGUN", "ONDO", "OSUN", "OYO",
    "PLATEAU", "RIVERS", "SOKOTO", "TARABA", "YOBE", "ZAMFARA",
    "ABUJA", "LA", "FCT",
}


def _looks_like_state(v: str) -> bool:
    """True when a value is a Nigerian state name or a safe short code."""
    return str(v).strip().upper() in _NG_STATE_NAMES
from rebuild_db import (DB_SCHEMA_SQL, TID_PATTERN, _flush_batch,
                        _clean_pandas_leak, _is_real_name, _is_real_tid,
                        _repair_code_names, _resolve_code_names,
                        _row_has_content, clean_date, clean_val,
                        detect_all_header_rows, normalize_col_name,
                        read_multiblock)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")

# Live build progress — written to data/build_progress.txt so the build can
# be watched from outside (tail the file) and reported as it runs. A single
# line is rewritten in place, so the file always holds the CURRENT state.
PROGRESS_FILE = PROJECT_ROOT / "data" / "build_progress.txt"
_last_progress = {"n": 0}


def _report_progress(msg: str, force: bool = False) -> None:
    """Print a \r progress line (flushed) and mirror it to the progress file.

    force=True always writes (used for phase boundaries); otherwise the
    line is throttled to ~10 updates/sec so fast sheets don't spam stdout.
    """
    _last_progress["n"] += 1
    if not force and _last_progress["n"] % 10 != 0:
        return
    sys.stdout.write("\r" + msg + " " * 12 + "\r")
    sys.stdout.flush()
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS_FILE.write_text(msg, encoding="utf-8")
    except OSError:
        pass


def _clear_progress() -> None:
    """Blank the progress line/file at the end of a build."""
    sys.stdout.write("\r" + " " * 100 + "\r")
    sys.stdout.flush()
    try:
        PROGRESS_FILE.unlink(missing_ok=True)
    except OSError:
        pass

# Derived/export files the app writes into the same folder (e.g. "Export to
# Excel" downloads). They are NOT source data — ingesting them would create
# duplicate rows and pollute the DB, so they are always excluded.
EXCLUDED_EXPORTS = {
    "medplus_tids.xlsx",
    "medplus_mids.xlsx",
}


# ── Excel discovery ───────────────────────────────────────────────────────

def find_excel_files(folder: Path) -> List[Path]:
    """Return every Excel file in the folder (recursively), sorted by name.

    Skips Excel temp lock files (``~$...xlsx``) that Windows/Excel leaves
    behind while a workbook is open, plus derived export files the app itself
    wrote into the folder (``EXCLUDED_EXPORTS``) so they never get re-ingested
    as sources.
    """
    files = []
    if not folder.exists():
        logger.error(f"  ❌ Folder not found: {folder}")
        return files
    for ext in EXCEL_EXTENSIONS:
        files.extend(p for p in folder.rglob(f"*{ext}")
                     if not p.name.startswith("~$")
                     and p.name.lower() not in EXCLUDED_EXPORTS)
    # Deduplicate (rglob per extension can't overlap, but sort for stable order)
    return sorted({p.resolve() for p in files})


def folder_snapshot(folder: Path) -> Dict[Path, Tuple[int, int]]:
    """Snapshot every Excel file's (mtime_ns, size) so changes can be detected."""
    snap: Dict[Path, Tuple[int, int]] = {}
    for f in find_excel_files(folder):
        try:
            st = f.stat()
            snap[f] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return snap


# ── Report-style sheet handling ────────────────────────────────────────────
# Some workbooks (MRSP_Merchants.xlsx, static_account_terminal.xlsx) are
# exported reports: a title/blank block sits ABOVE the real column header,
# so pandas' default header=row-0 read produces "Unnamed: N" columns and
# every row lands in an unmapped bucket (all fields empty in the DB).
# Detect the true header row and slice from there.

def _detect_header_row(raw_df) -> Optional[int]:
    """Find the row whose cells best match known schema columns.

    Scans the first 25 rows and returns the index of the row that normalizes
    the most cells to a known schema field (normalize_col_name changes the
    text). Requires at least 2 recognized columns AND a HIGH ratio of
    recognized-to-populated cells — a real header row is nearly all labels
    ("MERCHANT NAME", "MXCODE", "TID" → 80-100%), while a data row is mostly
    values (names, phones, addresses) that don't normalize (~10-20%), even
    though isolated values like "MX120925" or an email DO normalize.

    Returns None when no row qualifies, so the caller can fall back to the
    standard header=row-0 read or borrow a reference layout.
    """
    best_idx: Optional[int] = None
    best_score = 0
    for i in range(min(25, len(raw_df))):
        non_empty = 0
        score = 0
        for cell in raw_df.iloc[i]:
            v = clean_val(cell)
            if not v:
                continue
            non_empty += 1
            if normalize_col_name(v) != v:  # recognized as a schema column
                score += 1
        if non_empty == 0:
            continue
        ratio = score / non_empty
        if score >= 2 and ratio >= 0.5 and score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _workbook_reference_headers(xls) -> Optional[List[str]]:
    """Pick the workbook sheet whose header layout decodes headerless
    companion sheets (e.g. 2ISW "Sheet1" mirrors "2ISW_Parameter" but has no
    header row).

    Prefers the FIRST sheet with a strong header (>= 10 normalized columns)
    — the primary sheet of a workbook is almost always its canonical layout
    (2ISW_Parameter, Sheet1 of the NNPC master, etc.). Falls back to the
    max-score header when no sheet is that strong, then to None.
    """
    best_headers: Optional[List[str]] = None
    best_score = 0
    for sh in xls.sheet_names[:10]:
        try:
            raw = pd.read_excel(xls, sheet_name=sh, dtype=str,
                                keep_default_na=False, header=None)
        except Exception:
            continue
        raw = raw.dropna(axis=1, how="all")
        hdr = _detect_header_row(raw)
        if hdr is None:
            continue
        cells = raw.iloc[hdr].tolist()
        score = sum(1 for c in cells
                    if normalize_col_name(str(c)) != str(c).strip())
        # The primary sheet's layout is canonical — stop at the first strong
        # header instead of letting a secondary sheet (e.g. a 43-col
        # Sameday/NIBSS variant) win on raw column count.
        if score >= 10:
            return [str(c) for c in cells]
        if score > best_score:
            best_score = score
            best_headers = [str(c) for c in cells]
    return best_headers


# Value patterns used to score a headerless sheet against the reference
# layout. A correct alignment makes several reference columns line up with
# the value shapes those columns are supposed to hold (MX codes in the MX
# column, @ in the email column, digit-strings in phones, real names in the
# merchant-name column, TID-shaped codes in the TID column).
_BORROW_PATTERNS = [
    ("mxcode",        re.compile(r"^MX\d+")),
    ("email",         re.compile(r"@")),
    ("phone",         re.compile(r"^\+?\d{7,}$")),
    ("merchant_name", re.compile(r"[A-Za-z]{3,}\s+[A-Za-z]{2,}")),
    # Real TIDs are 8-char (2ISW166C), Ifis 2ISB.../Bank Mx 2xxx forms, or
    # 2103O338 — NOT short codes like 507 (terminal owner code). Shared
    # pattern with rebuild_db._is_real_tid so the two never drift apart.
    ("tid",           TID_PATTERN),
]


def _borrow_reference_headers(raw_df, reference_headers):
    """Align a headerless data sheet to a known reference layout by position.

    Some sheets stack TWO export blocks with different column layouts in one
    sheet (e.g. 2ISW 'Sheet1': rows 1-39 omit the MERCHANT ID column — every
    field shifted left by one — while rows 40+ match the reference exactly).
    A single uniform offset can never align both, so we score each data ROW
    independently: the row is assigned the horizontal offset at which its
    cells best match the value shapes those reference columns are supposed to
    hold (real names in merchant_name, MX codes in mxcode, @ in email,
    digit-strings in phone, TID-shaped codes in tid). Rows that score below
    the confidence threshold inherit the majority offset.

    Returns a DataFrame carrying the reference header names (original row
    indices preserved so workbook row numbers stay accurate), or None if no
    confident alignment exists.
    """
    if not reference_headers or not len(raw_df.columns):
        return None
    n_data = len(raw_df.columns)
    n_ref = len(reference_headers)

    # (field, pattern, ref_idx) for every pattern whose reference column exists
    ref_fields = []
    for field, pattern in _BORROW_PATTERNS:
        idx = next((i for i, h in enumerate(reference_headers)
                    if normalize_col_name(str(h)) == field), None)
        if idx is not None:
            ref_fields.append((field, pattern, idx))
    if not ref_fields:
        return None

    # 1) Score every row against every plausible offset
    row_offsets: List[Optional[int]] = []
    for i in range(len(raw_df)):
        row = raw_df.iloc[i]
        if not any(str(v).strip() for v in row):
            row_offsets.append(None)
            continue
        best_offset: Optional[int] = None
        best_hits = -1
        best_score = -1.0
        for offset in range(-2, 3):
            if not (0 <= 0 + offset < n_ref) or not (0 <= n_data - 1 + offset < n_ref):
                continue
            hits = 0
            score = 0.0
            for field, pattern, ref_idx in ref_fields:
                data_idx = ref_idx - offset
                if not (0 <= data_idx < n_data):
                    continue
                v = str(row.iloc[data_idx]).strip().upper()
                if not v:
                    continue
                if pattern.match(v):
                    hits += 1
                    score += 1.0
            if hits > best_hits or (hits == best_hits and score > best_score):
                best_offset = offset
                best_hits = hits
                best_score = score
        # A row needs >=2 independently-consistent columns to trust its offset
        row_offsets.append(best_offset if best_hits >= 2 else None)

    valid = [o for o in row_offsets if o is not None]
    if not valid:
        return None
    majority = max(set(valid), key=valid.count)

    # 2) Merge every row under the reference headers at its own offset
    aligned: List[List[str]] = []
    for i in range(len(raw_df)):
        offset = row_offsets[i] if row_offsets[i] is not None else majority
        row_out = [""] * n_ref
        for j in range(n_data):
            col = j + offset
            if 0 <= col < n_ref:
                row_out[col] = str(raw_df.iloc[i, j])
        if any(v.strip() for v in row_out):
            aligned.append((i, row_out))
    if not aligned:
        return None

    df = pd.DataFrame([r for _, r in aligned],
                      columns=[str(h) for h in reference_headers])
    df.index = [i for i, _ in aligned]  # keep original row numbers
    return df


def read_sheet_detected(xls, sheet_name,
                        reference_headers: Optional[List[str]] = None
                        ) -> Tuple[pd.DataFrame, int]:
    """Read a sheet, handling report-style layouts whose header row is not row 0.

    Returns (df, header_offset): df carries the detected header row's cells as
    column names and only the data rows below it; header_offset is the
    0-based index of the header row (0 = standard layout, -1 = a headerless
    sheet decoded against the reference layout). Row numbers in the DB are
    computed as header_offset + idx + 2 so they match the workbook.

    Headerless sheets (no detected header at all, e.g. 2ISW "Sheet1") are
    aligned against the workbook's reference layout by MX-column position.
    """
    raw = pd.read_excel(xls, sheet_name=sheet_name, dtype=str,
                        keep_default_na=False, header=None)
    raw = raw.dropna(axis=1, how="all")
    if raw.empty:
        return raw, 0

    # Stacked multi-block sheets (Change of merchant details, Deactivated
    # TID): MANY header rows, one per export block, each with its own layout.
    # Decode every block against its own header before falling back to the
    # single-header path below. header_offset=-1 so row numbers = raw+1.
    mb_headers = detect_all_header_rows(raw)
    if len(mb_headers) >= 2:
        mb = read_multiblock(raw, mb_headers)
        if not mb.empty:
            return mb, -1

    header_idx = _detect_header_row(raw)

    if header_idx is None:
        # Row 0 populated but sparse (e.g. Deactivated TID: TERMINAL ID /
        # REASON / REACTIVATION) is still a valid header — keep standard read.
        if any(clean_val(c) for c in raw.iloc[0]):
            header_idx = 0
        else:
            # Row 0 is blank → genuinely headerless. Try the reference layout
            # before falling back to empty synthetic headers.
            borrowed = _borrow_reference_headers(raw, reference_headers)
            if borrowed is not None:
                borrowed = borrowed.dropna(axis=1, how="all")
                return borrowed, -1
            header_idx = 0

    header_cells = raw.iloc[header_idx].tolist()
    # Blank header cells -> stable synthetic names so the record loop never
    # creates an empty-string key or pandas "Unnamed" collisions.
    clean_headers = [
        str(c).strip() if str(c).strip() else f"col_{i}"
        for i, c in enumerate(header_cells)
    ]
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = clean_headers
    df = df.reset_index(drop=True)
    df = df.dropna(axis=1, how="all")
    return df, header_idx


def _refine_column_mapping(df, col_mapping: Dict[str, str]) -> None:
    """Data-driven column refinements for report-style workbooks.

    - A column whose values are predominantly MX codes ("MX80254") holds the
      merchant's MX code, even if its header says "merchant code" — route it
      to mxcode so both the merchant id and the MX code survive.
    - A column headed "Static Bank Name" actually carries the real merchant
      trading name in these reports (e.g. "Genesis Foods Nigeria", "NIGERIA
      POLICE FORCE"). When the sheet has no other merchant_name source, route
      it to merchant_name so the name becomes searchable.
    - When a sheet has a real street-address column (PHYSICAL ADDR), LGA/LCDA
      columns (a district name like "IBADAN") must NOT steal the address field
      — they route to the dedicated lga field instead.
    """
    has_real_addr = any(
        col_mapping.get(c) == "address" and "physical addr" in str(c).lower()
        for c in df.columns)
    # Positional iteration: duplicate raw headers (e.g. two "col_N" blanks)
    # would make df[raw_col] return a DataFrame, not a Series.
    for j, raw_col in enumerate(df.columns):
        vals = [clean_val(v) for v in df.iloc[:, j].tolist()]
        vals = [v for v in vals if v]
        if not vals:
            continue
        mx_like = sum(1 for v in vals if re.match(r"^MX\d{3,}$", v.upper()))
        if mx_like / len(vals) >= 0.6:
            col_mapping[raw_col] = "mxcode"
            continue
        if ("bank name" in str(raw_col).lower()
                and col_mapping.get(raw_col) == "bank"):
            real = sum(1 for v in vals if _is_real_name(v))
            has_name_col = any(
                n == "merchant_name" for n in col_mapping.values())
            if not has_name_col and real / len(vals) >= 0.6:
                col_mapping[raw_col] = "merchant_name"
            continue
        low_hdr = str(raw_col).lower()
        if (has_real_addr and col_mapping.get(raw_col) == "address"
                and ("lga" in low_hdr or "lcda" in low_hdr)):
            # Keep the real street address; LGA/LCDA routes to the lga field.
            col_mapping[raw_col] = "lga"
        # Headerless state column (e.g. Medplus.xlsx's trailing blank-headed
        # column holding LAGOS/ABIA/…). No header = no keyword match, so sniff
        # the values: if an otherwise-unmapped column is predominantly Nigerian
        # state names (or safe short codes LA/FCT), route it to state.
        if col_mapping.get(raw_col) == raw_col:  # still unmapped
            state_like = sum(1 for v in vals if _looks_like_state(v))
            if len(vals) >= 3 and state_like / len(vals) >= 0.6:
                col_mapping[raw_col] = "state"


# ── Row ingestion ─────────────────────────────────────────────────────────

def build_intelligence_db(folder: Path, out_path: Path) -> bool:
    """Read every Excel sheet in the folder into a fresh intelligence.db.

    Returns True on success, False if the build could not complete (no Excel
    files found, or the target database is locked by another process).
    """
    logger.info("=" * 70)
    logger.info("  BUILDING intelligence.db FROM ALL EXCEL FILES")
    logger.info("=" * 70)

    excel_files = find_excel_files(folder)
    if not excel_files:
        logger.error("  ❌ No Excel files found in folder: %s", folder)
        return False

    logger.info(f"\n  Folder: {folder}")
    logger.info(f"  Excel files found: {len(excel_files)}")
    for f in excel_files:
        logger.info(f"    • {f.name}")

    # Fresh database
    if out_path.exists():
        try:
            out_path.unlink()
        except PermissionError:
            logger.error(
                f"\n  ❌ Cannot replace {out_path.name} — it is locked by another process."
                f"\n     Stop the running app/API (or close any tool using the file) and retry."
            )
            return False
        logger.info(f"\n  🗑️  Deleted old {out_path.name}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(out_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DB_SCHEMA_SQL)
    conn.commit()
    logger.info(f"  ✅ Created fresh {out_path.name} schema\n")

    c = conn.cursor()
    total_rows = 0
    files_ok = 0
    fts_rows_data: List[Tuple[int, Tuple]] = []
    batch: List[Tuple[int, Tuple]] = []
    next_id = 1  # autoincrement counter (explicit so FTS rowids line up)

    files_total = len(excel_files)
    for file_no, file_path in enumerate(excel_files, 1):
        _report_progress(
            f"[build] file {file_no}/{files_total}: {file_path.name} — reading…",
            force=True)
        try:
            xls = pd.ExcelFile(str(file_path))
        except Exception as exc:
            logger.warning(f"  [SKIP] {file_path.name} — cannot open: {exc}")
            continue

        # Reference layout for decoding headerless sheets (e.g. 2ISW "Sheet1"
        # mirrors "2ISW_Parameter" but has no header row).
        reference_headers = _workbook_reference_headers(xls)

        file_rows = 0
        for sheet_name in xls.sheet_names:
            try:
                df, header_offset = read_sheet_detected(
                    xls, sheet_name, reference_headers=reference_headers)
            except Exception as exc:
                logger.warning(f"    [ERROR] Sheet {sheet_name}: {exc}")
                continue

            if df.empty:
                continue

            # Map columns using the same keyword rules as rebuild_db
            col_mapping: Dict[str, str] = {
                raw_col: normalize_col_name(str(raw_col))
                for raw_col in df.columns
            }
            _refine_column_mapping(df, col_mapping)

            # NNPC-style workbooks: "DEALER NAME" holds the real trading name
            # while "MERCHANT NAME" is often just a code. Route dealer name to
            # contact_name by default, and prefer it as merchant_name when the
            # merchant name column is a code (mirrors import_nnpc.py).
            dealer_col = None
            for raw_col in df.columns:
                if "dealer name" in str(raw_col).lower():
                    dealer_col = raw_col
                    if col_mapping.get(raw_col) == raw_col:  # not already mapped
                        col_mapping[raw_col] = "contact_name"
                    break

            sheet_rows = 0
            sheet_len = len(df)
            for idx, row in df.iterrows():
                if sheet_rows % 250 == 0 and sheet_len:
                    _report_progress(
                        f"[build] {file_no}/{files_total} {file_path.name} :: "
                        f"{sheet_name} — {sheet_rows:,}/{sheet_len:,} rows "
                        f"({sheet_rows * 100 // sheet_len}%)")
                rec: Dict[str, Any] = {
                    "sheet_name": f"{file_path.stem} :: {sheet_name}",
                    "row_number": header_offset + idx + 2,
                }
                raw_parts: Dict[str, str] = {}
                for raw_col, norm in col_mapping.items():
                    val = _clean_pandas_leak(clean_val(row.get(raw_col, "")))
                    # Smart overwrite: for merchant_name, don't let metadata
                    # columns (category codes, LGA codes, acquirer IDs)
                    # overwrite a real name already captured.
                    if norm == "merchant_name" and _is_real_name(rec.get("merchant_name", "")):
                        if not _is_real_name(val):
                            raw_parts[str(raw_col)] = val
                            continue
                    # NNPC fallback: merchant name is a code but the dealer
                    # name column carries the real trading name.
                    if (norm == "merchant_name" and not _is_real_name(val)
                            and dealer_col is not None and raw_col != dealer_col):
                        dn = clean_val(row.get(dealer_col, ""))
                        if _is_real_name(dn):
                            val = dn
                    # Smart overwrite: for email, never let a non-address value
                    # (e.g. the EMAIL ALERTS Y/N flag column, which maps to the
                    # same field) clobber a real address already captured. Only
                    # an '@' value may replace an existing email.
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

                # Drop rows that are pure pandas-printout artifacts — every
                # field cleaned to empty (phantom rows from the multi-block
                # decoder where a leaked cell spilled into extra rows).
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
                    rec["sheet_name"], header_offset + idx + 2,
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
                    rec.get("merchant_category_code", ""),
                    rec.get("business_occupation_code", ""),
                    rec.get("terminal_owner_code", ""),
                    rec.get("settlement_type", ""),
                    rec.get("acquirer", ""),
                    rec.get("acquirer_id", ""),
                    rec.get("lga", ""),
                    rec.get("slip_footer", ""),
                    rec.get("tin", ""),
                    rec.get("mtn_serial", ""),
                    rec.get("sim9mobile_serial", ""),
                    rec.get("deployment_date", ""),
                    rec.get("bank_code", ""),
                )

                batch.append((next_id, row_tuple))
                next_id += 1
                sheet_rows += 1

                if len(batch) >= config.DB_BATCH_SIZE:
                    total_rows += _flush_batch(conn, batch, fts_rows_data)
                    batch = []

            if sheet_rows:
                logger.info(f"    [{file_path.name}] Sheet: {sheet_name:<28} → {sheet_rows:>4} rows")
            file_rows += sheet_rows

        if file_rows:
            files_ok += 1
            logger.info(f"  [{file_path.name}] Total: {file_rows:,} rows")
        else:
            logger.info(f"  [{file_path.name}] (no data rows)")
        _report_progress(
            f"[build] file {file_no}/{files_total} done — {file_rows:,} rows "
            f"(total so far: {total_rows + len(batch):,})",
            force=True)

    if batch:
        total_rows += _flush_batch(conn, batch, fts_rows_data)

    _report_progress(
        f"[build] all {files_total} files read — {total_rows:,} rows, "
        f"building indexes…", force=True)
    logger.info(f"\n  📊 Total rows inserted: {total_rows:,} across {files_ok} files")

    # Rebuild FTS indexes (porter + trigram) from collected data
    logger.info("\n  🔍 Rebuilding FTS5 index...")
    fts_count = 0
    trigram_count = 0
    for row_num, (mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi,
                  mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns,
                  sims, depd, bcode) in fts_rows_data:
        try:
            c.execute(
                """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id, merchant_category_code,
                   business_occupation_code, terminal_owner_code, settlement_type,
                   acquirer, acquirer_id, lga, slip_footer, tin, mtn_serial,
                   sim9mobile_serial, deployment_date, bank_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_num, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi,
                 mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns, sims,
                 depd, bcode)
            )
            fts_count += 1
        except sqlite3.IntegrityError:
            pass
        try:
            c.execute(
                """INSERT OR IGNORE INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id,
                   merchant_category_code, business_occupation_code,
                   terminal_owner_code, settlement_type, acquirer, acquirer_id,
                   lga, slip_footer, tin, mtn_serial, sim9mobile_serial,
                   deployment_date, bank_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_num, mn, sh, al, em, ph, ad, cn, td, mx, pc, an, mi,
                 mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns, sims,
                 depd, bcode)
            )
            trigram_count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    logger.info(f"  ✅ FTS5 index: {fts_count:,} entries")
    logger.info(f"  ✅ Trigram index: {trigram_count:,} entries")

    # Plain indexes
    logger.info("\n  📇 Creating indexes...")
    c.execute("CREATE INDEX IF NOT EXISTS idx_merchant_name ON merchants(merchant_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tid ON merchants(tid)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_email ON merchants(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_slip_header ON merchants(slip_header)")
    conn.commit()
    logger.info("  ✅ Indexes created")

    # Post-processing: fix numeric merchant_names using slip_header
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Repairing numeric merchant_names")
    logger.info("=" * 70)
    _repair_code_names(conn)

    # NNPC aggregator placeholders ("Interswitch Limited/NNPC 68") -> the real
    # dealer name captured in contact_name (DEALER NAME / CONTACTNAME columns).
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Recovering NNPC dealer names")
    logger.info("=" * 70)
    _repair_placeholder_names(conn)

    # NNPC aggregator placeholders: after names are repaired, resolve
    # bank/state codes to human-readable names (bank_code 214 → FCMB,
    # state_code LA → LAGOS) so the profile never shows bare codes.
    logger.info("\n" + "=" * 70)
    logger.info("  POST-PROCESSING: Resolving bank/state codes to names")
    logger.info("=" * 70)
    _resolve_code_names(conn)

    # Normalized name buckets (instant exact-normalized lookup + autocomplete)
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
    logger.info(f"\n  ✅ intelligence.db built — {total_rows:,} records\n")
    _clear_progress()
    return True


def watch_and_rebuild(folder: Path, out_path: Path, interval: float = 2.0,
                      verify: bool = True):
    """Watch the folder and rebuild intelligence.db whenever an Excel file changes.

    Polls the folder every `interval` seconds, compares each Excel file's
    (mtime, size) against the last seen state, and triggers a full rebuild
    (with optional verification) when anything changed. Stops on Ctrl+C.
    """
    logger.info("=" * 70)
    logger.info("  WATCH MODE — rebuilding intelligence.db on every change")
    logger.info("  Press Ctrl+C to stop.")
    logger.info("=" * 70)

    # Build once at startup so the DB is current before watching begins.
    logger.info("\n  🔄 Initial build...")
    ok = build_intelligence_db(folder, out_path)
    if ok and verify:
        if not verify_search(db_path=out_path):
            logger.error(
                "\n  ❌ Verification failed on the initial build — watch mode will keep running, "
                "but check the report-style header detection above.\n"
            )
    elif not ok:
        logger.warning("\n  ⚠️  Initial build skipped — will retry when a change is detected.\n")

    last = folder_snapshot(folder)
    if not last:
        logger.warning("\n  ⚠️  No Excel files found yet — watching for the first file...")

    try:
        while True:
            time.sleep(interval)
            current = folder_snapshot(folder)
            if current == last:
                continue

            # Debounce: wait one extra interval so a file that is mid-write
            # (size still changing) settles before we read it. Do NOT advance
            # `last` here — if we did, the change would be considered "seen"
            # while the file is still being written, and once it settles the
            # next poll would see current == last and never rebuild.
            time.sleep(interval)
            settled = folder_snapshot(folder)
            if settled != current:
                continue

            changed = {f for f in settled if settled.get(f) != last.get(f)}
            added = {f for f in settled if f not in last}
            removed = {f for f in last if f not in settled}

            names = sorted(p.name for p in (changed | added | removed))
            logger.info("\n" + "-" * 70)
            logger.info(f"  🔄 Change detected ({time.strftime('%H:%M:%S')}):")
            for n in names:
                logger.info(f"      • {n}")
            logger.info("-" * 70)

            ok = build_intelligence_db(folder, out_path)
            if ok and verify:
                if not verify_search(db_path=out_path):
                    logger.error(
                        "\n  ❌ Verification failed after rebuild — check the report-style "
                        "header detection above (e.g. MRSP / static_account_terminal).\n"
                    )
            elif not ok:
                logger.warning(
                    "\n  ⚠️  Build skipped (database locked?) — will retry on next change.\n")
            last = settled
    except KeyboardInterrupt:
        logger.info("\n\n  ⏹️  Watch stopped.\n")


def _repair_placeholder_names(conn) -> None:
    """Replace NNPC-master aggregator placeholders with the real dealer name.

    The NNpc parameter master workbook writes the generic aggregator name
    'Interswitch Limited/NNPC 68' into the MERCHANTNAME column while the real
    trading name (e.g. 'ELEYELE SS', 'OSEMEDEMI TRADING COMPANY...') lives in
    the DEALER NAME / CONTACTNAME columns (captured as contact_name). Rows
    whose merchant_name is this placeholder get merchant_name = contact_name
    when the contact is a real name — and the FTS5 indexes are synced so the
    recovered names are searchable immediately.

    Only the '/NNPC' aggregator form is repaired: the NIBSS/2ISW
    'INTERSWITCH LIMITED 55' rows are legitimate BNPL collection accounts
    (Interswitch IS the merchant there, and their contact is 'TOUCHPOINT
    SUPPORT N'), so they are deliberately left untouched.
    """
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM merchants
        WHERE UPPER(TRIM(merchant_name)) LIKE 'INTERSWITCH LIMITED/NNPC%'
          AND contact_name != ''
          AND UPPER(contact_name) NOT LIKE 'ISW-NNPC%'
          AND UPPER(contact_name) NOT LIKE 'INTERSWITCH%'
          AND UPPER(contact_name) NOT LIKE 'TOUCHPOINT%'
          AND UPPER(contact_name) NOT LIKE '%NNPC DEALER%'
    """)
    fixable = c.fetchone()[0]
    logger.info(f"\n  🔧 NNPC placeholder names with recoverable dealer name: {fixable:,}")
    if fixable == 0:
        logger.info("  ✅ Nothing to repair")
        return

    c.execute("""
        SELECT id, merchant_name, contact_name, slip_header, alias, email,
               phone, address, tid, mxcode, payable_code, account_name,
               merchant_id, merchant_category_code, business_occupation_code,
               terminal_owner_code, settlement_type, acquirer, acquirer_id,
               lga, slip_footer, tin, mtn_serial, sim9mobile_serial,
               deployment_date, bank_code
        FROM merchants
        WHERE UPPER(TRIM(merchant_name)) LIKE 'INTERSWITCH LIMITED/NNPC%'
          AND contact_name != ''
          AND UPPER(contact_name) NOT LIKE 'ISW-NNPC%'
          AND UPPER(contact_name) NOT LIKE 'INTERSWITCH%'
          AND UPPER(contact_name) NOT LIKE 'TOUCHPOINT%'
          AND UPPER(contact_name) NOT LIKE '%NNPC DEALER%'
    """)
    rows = c.fetchall()
    logger.info("  Examples (before fix):")
    for r in rows[:5]:
        logger.info(f"    id={r[0]}  name={r[1]!r}  ->  {r[2]!r}")

    c.execute("""
        UPDATE merchants
        SET merchant_name = contact_name
        WHERE UPPER(TRIM(merchant_name)) LIKE 'INTERSWITCH LIMITED/NNPC%'
          AND contact_name != ''
          AND UPPER(contact_name) NOT LIKE 'ISW-NNPC%'
          AND UPPER(contact_name) NOT LIKE 'INTERSWITCH%'
          AND UPPER(contact_name) NOT LIKE 'TOUCHPOINT%'
          AND UPPER(contact_name) NOT LIKE '%NNPC DEALER%'
    """)
    updated = c.rowcount
    conn.commit()
    logger.info(f"  ✅ Replaced {updated:,} placeholder merchant_names with real dealer names")

    logger.info("\n  🔍 Updating FTS5 indexes for repaired rows...")
    fts_updated = 0
    for r in rows:
        row_id, _, new_name, slip, alias, email, phone, address, tid, mxcode, \
            payable_code, account_name, merchant_id, \
            mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns, sims, depd, \
            bcode = r
        try:
            # FTS5 virtual tables reject UPSERT (ON CONFLICT) — DELETE then
            # INSERT is the supported replace pattern (same as _repair_code_names).
            conn.execute("DELETE FROM merchants_fts WHERE rowid = ?", (row_id,))
            conn.execute(
                """INSERT INTO merchants_fts(rowid, merchant_name, slip_header, alias,
                   email, phone, address, contact_name, tid, mxcode, payable_code,
                   account_name, merchant_id, merchant_category_code,
                   business_occupation_code, terminal_owner_code, settlement_type,
                   acquirer, acquirer_id, lga, slip_footer, tin, mtn_serial,
                   sim9mobile_serial, deployment_date, bank_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, new_name, slip, alias, email, phone, address, new_name,
                 tid, mxcode, payable_code, account_name, merchant_id,
                 mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns, sims,
                 depd, bcode)
            )
            conn.execute("DELETE FROM merchants_fts_trigram WHERE rowid = ?", (row_id,))
            conn.execute(
                """INSERT INTO merchants_fts_trigram(rowid, merchant_name,
                   slip_header, alias, email, phone, address, contact_name, tid,
                   mxcode, payable_code, account_name, merchant_id,
                   merchant_category_code, business_occupation_code,
                   terminal_owner_code, settlement_type, acquirer, acquirer_id,
                   lga, slip_footer, tin, mtn_serial, sim9mobile_serial,
                   deployment_date, bank_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_id, new_name, slip, alias, email, phone, address, new_name,
                 tid, mxcode, payable_code, account_name, merchant_id,
                 mcc, boc, toc, st, acq, acid, lga, sft, tin, mtns, sims,
                 depd, bcode)
            )
            fts_updated += 1
        except Exception as exc:
            logger.warning(f"FTS sync failed for row {row_id}: {exc}")
    conn.commit()
    logger.info(f"  ✅ FTS indexes synced for {fts_updated:,} repaired rows")


def verify_search(db_path: Optional[Path] = None) -> bool:
    """Smoke-test the newly built intelligence.db.

    Returns True when every check passes, False if any sample search finds no
    results or the MRSP report-file proof fails — so the build script can
    surface a regression (e.g. broken report-style header detection) instead
    of quietly succeeding.
    """
    from merchant_intelligence import MerchantSearch
    import sqlite3 as _sq

    # Verify the ACTUAL database we just built (not just the default active_db)
    searcher = MerchantSearch(db_path=db_path)
    ok = True
    logger.info("\n" + "=" * 70)
    logger.info("  VERIFICATION: Sample searches against intelligence.db")
    logger.info("=" * 70)

    # Sample queries include a real phone pulled from the freshly built DB
    # (the repo never hardcodes merchant contact data).
    _sample_phone = ""
    try:
        import sqlite3 as _sql
        _c = _sql.connect(db_path)
        _row = _c.execute(
            "SELECT phone FROM merchants WHERE phone LIKE '080%' "
            "AND length(phone)=11 LIMIT 1").fetchone()
        _c.close()
        if _row:
            _sample_phone = _row[0]
    except Exception:
        pass
    queries = ["LAGOON WATERS LTD", "THE FILM HOUSE", "MX183544"]
    if _sample_phone:
        queries.insert(2, _sample_phone)
    for query in queries:
        logger.info(f"\n  🔍 Query: {query}")
        results = searcher.search(query, limit=3, min_score=0)
        if results:
            for res in results[:3]:
                score = round(res.overall_score / 10, 1)
                name = res.record.get("merchant_name", "")[:55]
                sheet = res.record.get("sheet_name", "")[:28]
                logger.info(f"    {score:4.1f}/10  {name:55s}  [{sheet}]")
        else:
            logger.warning(f"  ⚠️  No results for sample query: {query}")
            ok = False

    # MRSP report file proof: MRSP_Merchants.xlsx is a report-style workbook
    # (title rows above the real header) whose rows carry a 2ISW merchant id
    # + MX code but no merchant name. Searching an MX code that ONLY exists in
    # that file (e.g. MX80254) must resolve to a record sourced from MRSP — if
    # the header detection ever regresses, this row lands in an unmapped bucket
    # and this check fails, so future rebuilds prove the report file loaded.
    logger.info("\n" + "-" * 70)
    logger.info("  MRSP REPORT FILE CHECK")
    logger.info("-" * 70)
    mrsp_results = searcher.search("MX80254", limit=5, min_score=0)
    mrsp_hit = next(
        (r for r in mrsp_results
         if "MRSP" in r.record.get("sheet_name", "").upper()),
        None,
    )
    if mrsp_hit:
        rec = mrsp_hit.record
        logger.info(
            f"  ✅ MRSP loaded — MX80254 → merchant_id={rec.get('merchant_id', '')} "
            f"mxcode={rec.get('mxcode', '')} [{rec.get('sheet_name', '')}]"
        )
    else:
        logger.error(
            "  ❌ MRSP check FAILED — MX80254 did not resolve to an MRSP record. "
            "The MRSP_Merchants.xlsx report file did not load correctly "
            "(check the report-style header detection above)."
        )
        ok = False

    # NNPC master sheet proof: the workbook stores the real dealer name in the
    # DEALER NAME / CONTACTNAME columns while MERCHANTNAME holds the generic
    # aggregator placeholder "Interswitch Limited/NNPC N". If the placeholder
    # recovery ever regresses, searching a dealer that ONLY exists in this file
    # (e.g. ELEYELE SS on MX184404) still finds the placeholder instead of the
    # real name — so this check fails and the rebuild surfaces the bug.
    logger.info("\n" + "-" * 70)
    logger.info("  NNPC MASTER SHEET CHECK (dealer-name recovery)")
    logger.info("-" * 70)
    nm_conn = _sq.connect(str(db_path))
    try:
        nm = nm_conn.execute(
            "SELECT merchant_name, contact_name, mxcode FROM merchants "
            "WHERE UPPER(TRIM(mxcode)) = 'MX184404' LIMIT 3"
        ).fetchall()
        good = any(
            str(r[0]).strip().upper() == "ELEYELE SS"
            or ("ELEYELE" in str(r[0]).upper())
            for r in nm
        )
        if good:
            logger.info(f"  ✅ NNPC master OK — MX184404 → {nm[0][0]} "
                        f"(contact: {nm[0][1]})")
        else:
            logger.error(
                "  ❌ NNPC master check FAILED — MX184404 merchant_name is still "
                "the generic placeholder (dealer-name recovery regressed)."
            )
            ok = False
    finally:
        nm_conn.close()

    # Change-of-details sheet proof: the sheet stacks many export blocks each
    # with its own header, so a mis-decoded block misaligns every column. A
    # merchant known to appear there (WHITEVILL HOTEL, MX45173) must resolve to
    # a row sourced from the Change sheet carrying a REAL tid (2ISWF0xx) and a
    # real account number — not a leaked header row or a shifted column.
    logger.info("\n" + "-" * 70)
    logger.info("  CHANGE-OF-DETAILS SHEET CHECK")
    logger.info("-" * 70)
    ch_conn = _sq.connect(str(db_path))
    try:
        ch = ch_conn.execute(
            "SELECT merchant_name, tid, account_number, bank, email, mxcode "
            "FROM merchants WHERE sheet_name LIKE '%Change%' "
            "AND UPPER(TRIM(mxcode)) = 'MX45173' LIMIT 3"
        ).fetchall()
        if ch and ch[0][1] and str(ch[0][1]).startswith("2ISW"):
            logger.info(f"  ✅ Change sheet OK — MX45173 → tid={ch[0][1]} "
                        f"acc={ch[0][2]} bank={ch[0][3]} [{ch[0][0]}]")
        else:
            logger.error(
                "  ❌ Change sheet check FAILED — MX45173 rows missing a real "
                "TID. The multi-block Change sheet did not decode correctly "
                "(each export block must be read against its own header)."
            )
            ok = False
    finally:
        ch_conn.close()

    # Max-info proof: the new high-value columns (MCC, settlement type,
    # terminal owner code, LGA, TIN, bank code, deployment date) must be
    # populated for the sheets that carry them. If the column mapping ever
    # regresses, these counts collapse to 0 and the rebuild surfaces it.
    logger.info("\n" + "-" * 70)
    logger.info("  MAX-INFO COLUMN CHECK (MCC / settlement / owner / LGA / TIN)")
    logger.info("-" * 70)
    mi_conn = _sq.connect(str(db_path))
    try:
        checks = [
            ("merchant_category_code", "MCC (2ISW_Parameter)",
             "SELECT COUNT(*) FROM merchants WHERE merchant_category_code != '' "
             "AND sheet_name LIKE '2ISW_Parameter_File 5 :: 2ISW_Parameter%'"),
            ("settlement_type", "Settlement type (Sameday)",
             "SELECT COUNT(*) FROM merchants WHERE settlement_type != '' "
             "AND sheet_name LIKE '%Sameday%'"),
            ("terminal_owner_code", "Terminal owner code",
             "SELECT COUNT(*) FROM merchants WHERE terminal_owner_code != ''"),
            ("lga", "LGA/LCDA",
             "SELECT COUNT(*) FROM merchants WHERE lga != ''"),
            ("tin", "TIN (NIBSS/Ifis)",
             "SELECT COUNT(*) FROM merchants WHERE tin != ''"),
            ("bank_code", "Bank code",
             "SELECT COUNT(*) FROM merchants WHERE bank_code != ''"),
            ("mtn_serial", "MTN serial (Deployment)",
             "SELECT COUNT(*) FROM merchants WHERE mtn_serial != ''"),
            ("deployment_date", "Deployment date",
             "SELECT COUNT(*) FROM merchants WHERE deployment_date != ''"),
        ]
        all_ok = True
        for col, label, sql in checks:
            n = mi_conn.execute(sql).fetchone()[0]
            status = "✅" if n else "❌"
            if not n:
                all_ok = False
            logger.info(f"  {status} {label:<34} {n:>7,} rows")
        if not all_ok:
            logger.error(
                "  ❌ Max-info check FAILED — one or more high-value columns "
                "loaded zero rows. Check the column-mapping rules."
            )
            ok = False
    finally:
        mi_conn.close()
    return ok


def main():
    parser = argparse.ArgumentParser(description="Build intelligence.db from a folder of Excel files.")
    parser.add_argument("--folder", type=Path, default=config.DATA_DIR,
                        help="Folder to scan for Excel files (default: data/)")
    parser.add_argument("--out", type=Path, default=config.INTELLIGENCE_DB,
                        help="Output database path (default: data/intelligence.db)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch the folder and auto-rebuild whenever an Excel file changes")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Poll interval in seconds for --watch (default: 2.0)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the sample-search verification after each build (faster in watch mode)")
    args = parser.parse_args()

    if args.watch:
        watch_and_rebuild(args.folder, args.out, interval=args.interval,
                          verify=not args.no_verify)
        return

    ok = build_intelligence_db(args.folder, args.out)
    if not ok:
        logger.error("\n  ❌ Build did not complete — verification skipped. Fix the issue above and retry.")
        sys.exit(1)
    verified = verify_search(db_path=args.out)
    logger.info("\n" + "=" * 70)
    if not verified:
        logger.error(
            "  ❌ VERIFICATION FAILED — one or more sample checks did not pass.\n"
            "     The database was built, but a report file may not have loaded correctly."
        )
    else:
        logger.info("  ✅ DONE — intelligence.db is ready. Restart the API to load it.")
    logger.info("=" * 70)

    # Governed-data slice: append this run to the ingestion ledger (dedicated
    # data/ingest_ledger.db, survives rebuilds) with the source snapshot so
    # freshness can be computed later. Never breaks the build.
    try:
        from merchant_intelligence.ingest_ledger import record
        row_count = 0
        try:
            import sqlite3 as _sqlite3
            _c = _sqlite3.connect(str(args.out), timeout=10)
            try:
                row_count = int(_c.execute("SELECT COUNT(*) FROM merchants").fetchone()[0])
            finally:
                _c.close()
        except Exception:  # noqa: BLE001
            row_count = 0
        record("build_intelligence_db",
               "ok" if verified else "failed",
               detail=f"build {'verified' if verified else 'verification failed'} via {args.folder.name}",
               row_count=row_count,
               sources=folder_snapshot(args.folder))
    except Exception as _e:  # noqa: BLE001
        logger.warning("ingest ledger record skipped: %s", _e)

    sys.exit(0 if verified else 1)


if __name__ == "__main__":
    main()
