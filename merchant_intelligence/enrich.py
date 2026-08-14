"""
enrich.py — Build-time enrichment of the merchant database.

After every Excel row is loaded, two derived layers are computed so the app
can answer questions the raw rows cannot answer on their own:

1. quality_score / quality_flags — a per-record data-quality score (0-100)
   plus the list of issues that caused deductions:

       missing_email      (-20)  no real '@' address on this record
       missing_phone      (-20)  no mobile/telephone captured
       missing_account    (-15)  no settlement account number
       missing_address    (-10)  no physical address
       name_conflict      (-15)  different FILES name the same terminal
                                 differently, and the identifier signatures
                                 differ (i.e. the registry disagrees about
                                 who owns that terminal — e.g. JUST CHIPS vs
                                 OLAWALE ODUOLA sharing MX154553)
       shared_identifier  (-10)  a phone/email/MX value is carried by rows
                                 of DIFFERENT merchants (different names AND
                                 different identifier signatures) — the value
                                 is duplicated across unrelated records

   A record that is the SAME merchant under two names (LAGOON WATERS LTD vs
   "Interswitch Limited/NNPC 15" share full (tid, mx, mid, account) tuples)
   is NOT a conflict — that is benign duplicate naming, and it costs nothing.
   The discriminator is the same full-identifier signature the relationship
   guard uses (entity._names_share_signature), so quality and the graph never
   disagree.

2. merchant_events — a per-terminal timeline. Rows are grouped by their
   strongest stable identifier (tid -> mxcode -> merchant_id ->
   account_number). For each group the builder emits:

       first_seen     earliest onboarded_date (or earliest source file)
       last_seen      latest onboarded_date (or latest source file)
       name_variant   every distinct merchant_name with its source file
       account_change old->new account transitions parsed from the
                      Change-of-details sheet rows' raw_data
                      (OLD BANK ACC NO / NEW BANK ACC NO / OLD & NEW BANK)

   The app queries this table to render a Timeline tab on the Profile page —
   the SPAR -> ARTEE INDUSTRIES style tracing, with dates and sources.

Both layers are derived from the DB itself, so they always agree with what
the search engine sees. Called at the end of build_intelligence_db.py and
rebuild_db.py via enrich_database().
"""
import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Deduction catalogue ───────────────────────────────────────────────────
# (flag, human label, points lost). Order matters only for display.
_MISSING_EMAIL = ("missing_email", "no email address", 20)
_MISSING_PHONE = ("missing_phone", "no phone number", 20)
_MISSING_ACCOUNT = ("missing_account", "no settlement account number", 15)
_MISSING_ADDRESS = ("missing_address", "no physical address", 10)
_NAME_CONFLICT = ("name_conflict",
                  "different files name this terminal differently", 15)
_SHARED_IDENTIFIER = ("shared_identifier",
                      "identifier shared with an unrelated merchant", 10)

_IDENTITY_FIELDS = ("tid", "mxcode", "merchant_id", "account_number")

# Link values that, when shared by DIFFERENT merchants, are a data-quality
# smell worth flagging (phones/emails/MX codes should identify one merchant).
_SHARED_FIELDS = ("phone", "email", "mxcode")

_SKIP_CELLS = {"", "y", "n", "n/a", "na", "-", "nil", "none", "null"}


def _clean(v: Any) -> str:
    s = str(v or "").strip()
    return "" if s.lower() in _SKIP_CELLS else s


def _norm_name(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().upper())


def _term_key(row: Dict[str, Any]) -> Tuple[str, str]:
    """Strongest stable identifier for a row: (field, value) or ('', '')."""
    for field in _IDENTITY_FIELDS:
        v = _clean(row.get(field))
        if v:
            return field, v
    return "", ""


def _signature(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """Full identifier signature (tid, mxcode, merchant_id, account_number).

    Rows with fewer than 2 non-empty identity fields are excluded from
    signature connectivity — a sparse row can never prove same-merchant-ness.
    Mirrors entity._names_share_signature so quality and graph agree.
    """
    sig = tuple(_clean(row.get(f)) for f in _IDENTITY_FIELDS)
    if sum(1 for s in sig if s) < 2:
        return ("", "", "", "")
    return sig  # type: ignore[return-value]


def _ensure_column(conn: sqlite3.Connection, table: str,
                   column: str, ddl: str) -> None:
    """Add a column if the table doesn't already have it (idempotent)."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ── Quality scoring ───────────────────────────────────────────────────────

def _score_row(rec: Dict[str, Any],
               conflicts: set, shared_vals: set) -> Tuple[float, List[str]]:
    """Score one record: 100 minus deductions, with the flag list."""
    flags: List[str] = []
    score = 100.0

    def deduct(flag: str, label: str, points: int) -> None:
        nonlocal score
        flags.append(flag)
        score -= points

    if "@" not in _clean(rec.get("email")):
        deduct(*_MISSING_EMAIL)
    if not _clean(rec.get("phone")):
        deduct(*_MISSING_PHONE)
    if not _clean(rec.get("account_number")):
        deduct(*_MISSING_ACCOUNT)
    if not _clean(rec.get("address")):
        deduct(*_MISSING_ADDRESS)

    rid = rec.get("id")
    if rid in conflicts:
        deduct(*_NAME_CONFLICT)
    # Shared-identifier smell: this row carries a value that another
    # DIFFERENT merchant also carries.
    for f in _SHARED_FIELDS:
        v = _clean(rec.get(f))
        if v and (f, v) in shared_vals:
            deduct(*_SHARED_IDENTIFIER)
            break

    return max(round(score), 0), flags


def compute_quality(conn: sqlite3.Connection) -> int:
    """Compute quality_score / quality_flags for every merchants row.

    Cross-row signals (name_conflict, shared_identifier) are resolved first
    by grouping on terminal key and identifier signature, then each row is
    scored and written back. Returns the number of rows updated.
    """
    _ensure_column(conn, "merchants", "quality_score", "REAL DEFAULT 100")
    _ensure_column(conn, "merchants", "quality_flags", "TEXT DEFAULT '[]'")

    # Build connections don't set row_factory — dict(row) needs column names.
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, merchant_name, sheet_name, tid, mxcode, merchant_id, "
        "account_number, email, phone, address FROM merchants")]
    if not rows:
        return 0

    # Group rows by terminal key.
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for rec in rows:
        by_key[_term_key(rec)].append(rec)

    conflicts: set = set()
    shared_vals: set = set()
    for key, group in by_key.items():
        field, value = key
        if not field:
            continue
        # Distinct normalized names per signature class.
        names_by_sig: Dict[Tuple[str, str, str, str], set] = defaultdict(set)
        for rec in group:
            sig = _signature(rec)
            n = _norm_name(rec.get("merchant_name"))
            if n:
                names_by_sig[sig].add(n)
        if len(names_by_sig) <= 1:
            # One signature class => same merchant under possibly many names
            # (LAGOON WATERS LTD / Interswitch NNPC 15/16). Benign.
            continue
        # More than one signature class with different names => the registry
        # disagrees about this terminal. Flag every row in the group.
        for rec in group:
            conflicts.add(rec["id"])
        # Also: a phone/email/MX shared across the differing names is a
        # duplicated-identifier smell.
        for f in _SHARED_FIELDS:
            for rec in group:
                v = _clean(rec.get(f))
                if v:
                    shared_vals.add((f, v))

    # Score + write back in batches.
    conn.execute("BEGIN")
    updated = 0
    for rec in rows:
        score, flags = _score_row(rec, conflicts, shared_vals)
        conn.execute(
            "UPDATE merchants SET quality_score=?, quality_flags=? WHERE id=?",
            (score, json.dumps(flags), rec["id"]))
        updated += 1
    conn.commit()
    logger.info(f"  ✅ quality scores computed for {updated:,} records")
    return updated


# ── Terminal timeline (merchant_events) ───────────────────────────────────

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_key TEXT NOT NULL,
    key_field    TEXT NOT NULL,
    event_type   TEXT NOT NULL,   -- first_seen|last_seen|name_variant|account_change
    value        TEXT NOT NULL,   -- the date, name, or account number
    meta         TEXT NOT NULL,   -- JSON: {source, count, old, new, bank, date}
    occurred_at  TEXT             -- when it happened, if known (sortable)
);
CREATE INDEX IF NOT EXISTS idx_merchant_events_key
    ON merchant_events(terminal_key, event_type);
"""

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _date_key(v: str) -> Optional[str]:
    """Normalise a date-ish string to YYYY-MM-DD (sortable) or None."""
    v = (v or "").strip()
    if not v:
        return None
    if _DATE_RE.match(v):
        return v
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%b %Y", "%B %Y",
                "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _source_sheet(sheet: str) -> str:
    """File name of a 'file :: sheet' source string."""
    return (sheet or "").split("::")[0].strip()


def _parse_change_row(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract an old->new account change from a Change-of-details row.

    The multi-block Change sheet maps OLD/NEW BANK ACC NO into the union
    header, but the mapped row only keeps ONE account_number (last-write-wins
    among non-empty). The original OLD/NEW values survive in raw_data, so
    that is the ground truth here.
    """
    sheet = str(rec.get("sheet_name") or "")
    if "change" not in sheet.lower():
        return None
    try:
        raw = json.loads(rec.get("raw_data") or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    def find(keyword: str) -> str:
        for k, v in raw.items():
            if keyword in str(k).lower():
                return _clean(v)
        return ""

    old_acc = find("old bank acc")
    new_acc = find("new bank acc")
    if not old_acc and not new_acc:
        return None
    # Bank codes (NIBSS 057 etc.) live in keys that are NOT acc keys — the
    # 'old bank' fragment above would also match 'OLD BANK ACC NO'.
    old_bank = find_bank_code(raw, "old")
    new_bank = find_bank_code(raw, "new")
    date = _date_key(find("month of request") or find("request date")
                     or find("date of request") or find("date created"))
    return {
        "old_acc": old_acc,
        "new_acc": new_acc,
        "old_bank": old_bank,
        "new_bank": new_bank,
        "date": date,
        "source": sheet,
    }


def find_bank_code(raw: Dict[str, Any], side: str) -> str:
    """Find 'OLD BANK CODE' / 'NEW BANK CODE' (NIBSS codes, e.g. 057)."""
    kw = f"{side} bank"
    for k, v in raw.items():
        low = str(k).lower()
        if kw in low and "acc" not in low:
            return _clean(v)
    return ""


def build_events(conn: sqlite3.Connection) -> int:
    """Derive the merchant_events timeline from the loaded merchants table.

    Rebuilds the table from scratch (DROP + CREATE) so a fresh build never
    carries stale events. Returns the number of events written.
    """
    conn.executescript("DROP TABLE IF EXISTS merchant_events;")
    conn.executescript(_EVENTS_SCHEMA)

    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, sheet_name, row_number, merchant_name, tid, mxcode, "
        "merchant_id, account_number, email, phone, onboarded_date, raw_data "
        "FROM merchants")]
    if not rows:
        return 0

    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for rec in rows:
        by_key[_term_key(rec)].append(rec)

    conn.execute("BEGIN")
    n = 0
    for (field, value), group in by_key.items():
        if not field:
            continue
        key = value
        dates = [d for d in (_date_key(r.get("onboarded_date"))
                             for r in group) if d]
        sheets = [_source_sheet(r.get("sheet_name")) for r in group]

        def emit(etype: str, val: str, meta: Dict[str, Any],
                 occurred: Optional[str] = None) -> None:
            nonlocal n
            conn.execute(
                "INSERT INTO merchant_events "
                "(terminal_key, key_field, event_type, value, meta, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, field, etype, val, json.dumps(meta, default=str),
                 occurred))
            n += 1

        if dates:
            emit("first_seen", min(dates), {"source": sheets[0]}, min(dates))
            emit("last_seen", max(dates), {"source": sheets[-1]}, max(dates))
        else:
            # No dates anywhere — anchor on source-file order instead.
            emit("first_seen", "unknown",
                 {"source": sheets[0] if sheets else ""})
            emit("last_seen", "unknown",
                 {"source": sheets[-1] if sheets else ""})

        # Distinct name variants with their first source + row count.
        counters: Dict[str, Counter] = defaultdict(Counter)
        first_src: Dict[str, str] = {}
        for rec in group:
            nname = _norm_name(rec.get("merchant_name"))
            if not nname:
                continue
            counters[nname][_source_sheet(rec.get("sheet_name"))] += 1
            first_src.setdefault(nname, _source_sheet(rec.get("sheet_name")))
        for nname, src_counter in counters.items():
            emit("name_variant", nname,
                 {"source": first_src[nname],
                  "count": sum(src_counter.values()),
                  "files": dict(src_counter)})

        # Account changes from the Change-of-details sheet (raw_data holds
        # the OLD/NEW values the mapped columns can't).
        for rec in group:
            chg = _parse_change_row(rec)
            if not chg:
                continue
            detail = " → ".join(
                x for x in (chg["old_acc"], chg["new_acc"]) if x)
            emit("account_change", detail,
                 {k: v for k, v in chg.items() if v}, chg["date"])

    conn.commit()
    logger.info(f"  ✅ merchant_events timeline built ({n:,} events)")
    return n


# ── Orchestration ─────────────────────────────────────────────────────────

def enrich_database(conn: sqlite3.Connection) -> Dict[str, int]:
    """Run the full build-time enrichment (quality + timeline).

    Safe to call on any merchants schema (columns and the events table are
    created idempotently). Sets conn.row_factory = sqlite3.Row as a side
    effect (harmless on a build connection that closes right after).
    Returns {"quality_rows": n, "events": n}.
    """
    q = compute_quality(conn)
    e = build_events(conn)
    return {"quality_rows": q, "events": e}


# ── Timeline queries (app side) ───────────────────────────────────────────

def timeline_for(conn: sqlite3.Connection,
                 terminal_key: str) -> List[Dict[str, Any]]:
    """All events for one terminal key, oldest first."""
    rows = conn.execute(
        "SELECT event_type, value, meta, occurred_at "
        "FROM merchant_events WHERE terminal_key = ? "
        "ORDER BY occurred_at IS NULL, occurred_at, id",
        (terminal_key,)).fetchall()
    out = []
    for r in rows:
        ev = {"type": r[0], "value": r[1], "occurred_at": r[3]}
        try:
            ev["meta"] = json.loads(r[2] or "{}")
        except (ValueError, TypeError):
            ev["meta"] = {}
        out.append(ev)
    return out


def _like_escape(q: str) -> str:
    """Escape LIKE wildcards so user input is literal, not a pattern."""
    return (q or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def keys_for_query(conn: sqlite3.Connection, query: str,
                   limit: int = 20) -> List[Tuple[str, str]]:
    """Distinct (key_field, terminal_key) candidates for a fragment.

    Resolves name/identifier fragments to the terminal keys the registry
    actually stores — the DB is the ground truth (never invent a key).

    Two passes:
      1. Identifier fragments ("2ISW916B", "MX141692") — rows whose own
         tid/mxcode/merchant_id/account_number contains the fragment.
      2. Name fragments ("LAGOON WATERS") — rows whose merchant_name
         contains the fragment, then every identity field of those rows is
         collected. This matters for merchants whose rows carry ONLY an MX
         code / MID / account number (no real TID): their timeline key must
         still resolve, or the Timeline tab would silently stay empty.
    """
    conn.row_factory = sqlite3.Row
    q = (query or "").strip()
    if not q:
        return []
    keys: List[Tuple[str, str]] = []
    seen: set = set()
    like = f"%{_like_escape(q.upper())}%"
    # Pass 1: identity fields hold the fragment itself.
    for field in _IDENTITY_FIELDS:
        for row in conn.execute(
                f"SELECT DISTINCT {field} FROM merchants "
                "WHERE UPPER(TRIM({f})) LIKE ? ESCAPE '\\' "
                "AND TRIM({f}) != '' LIMIT ?".format(f=field),
                (like, limit)):
            v = str(row[0]).strip()
            if v and (field, v) not in seen:
                seen.add((field, v))
                keys.append((field, v))
    # Pass 2: name matches — collect EVERY identity field of the matched
    # rows so MX/MID/account-only merchants still resolve to their key.
    for row in conn.execute(
            "SELECT tid, mxcode, merchant_id, account_number FROM merchants "
            "WHERE UPPER(TRIM(merchant_name)) LIKE ? ESCAPE '\\' LIMIT ?",
            (like, limit * 3)):
        for field in _IDENTITY_FIELDS:
            v = str(row[field] or "").strip()
            if v and (field, v) not in seen:
                seen.add((field, v))
                keys.append((field, v))
    return keys[:limit]
