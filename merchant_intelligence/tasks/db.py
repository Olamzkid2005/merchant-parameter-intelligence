"""
db.py — SQLite registry access for the task engine.

Connection lifecycle (_connect), value normalisation (_norm / _fetch) and the
identifier-resolution queries the pipelines run: resolve_mx, resolve_any,
static_accounts_for_acc / static_accounts_for_mx, plus the pasted-name vs
registry-name status helper (_name_status / _name_for).
"""
import sqlite3
from typing import Any, Dict, List, Tuple

from .. import config
from ..fuzzy import confusable_key, confusable_variants, token_sort_ratio

def _connect():
    path = config.INTELLIGENCE_DB
    if not path.exists():
        raise FileNotFoundError(f"intelligence.db not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _norm(v):
    return str(v or "").strip().upper()


def _fetch(conn, sql, params):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _expand_confusables(values: List[str]) -> List[str]:
    """Widen a value list with confusable spellings (0↔O, 1↔I, …), deduped.

    The DB is the ground truth: a widened spelling only matches a row that
    ACTUALLY stores it, so TIDs typed as digits still resolve the rows stored
    with look-alike letters ('21030265' -> the '2103O265' row)."""
    out: List[str] = []
    for v in values:
        for ev in confusable_variants(v):
            if ev not in out:
                out.append(ev)
    return out


def resolve_mx(conn, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Map TID -> {mxcode, merchant_name, ...} (first best row per TID)."""
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    expanded = _expand_confusables(ids)
    q = ",".join("?" for _ in expanded)
    rows = _fetch(
        conn,
        f"SELECT tid, mxcode, merchant_name, sheet_name FROM merchants "
        f"WHERE UPPER(TRIM(tid)) IN ({q})",
        [i.upper().strip() for i in expanded],
    )
    for r in rows:
        key = _norm(r["tid"])
        if key and key not in out:
            out[key] = r
    return out


# Columns searched by resolve_any. static_acc_no is deliberately separate
# from account_number — in this workbook the two 10-digit value sets have
# ZERO overlap, so both must be searched.
RESOLVE_COLS = ("tid", "mxcode", "phone", "email", "account_number",
                "static_acc_no", "payable_code", "bvn", "merchant_id", "alias")


def resolve_any(conn, values: List[str]) -> Dict[str, Dict[str, Any]]:
    """Resolve identifiers of any kind (TID/MX/phone/email/static account /
    account number/payable/bvn/mid/alias) to a full registry row.

    Output is keyed by the QUERY value (upper-cased) so callers can look up
    `resolved.get(v.upper().strip())`; confusable spellings (0↔O, 1↔I, …)
    resolve too, because the widened values only match rows that store them.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not values:
        return out
    expanded = _expand_confusables(values)
    q = ",".join("?" for _ in expanded)
    where = " OR ".join(f"UPPER(TRIM({c})) IN ({q})" for c in RESOLVE_COLS)
    rows = _fetch(
        conn,
        f"SELECT id, merchant_name, tid, mxcode, phone, email, contact_name, "
        f"address, account_name, account_number, payable_code, alias, bvn, "
        f"merchant_id, static_acc_no, sheet_name, state, bank, onboarded_date, "
        f"slip_header, terminal_serial "
        f"FROM merchants WHERE {where}",
        expanded * len(RESOLVE_COLS),
    )
    # Pass 1: EXACT matches only. A value that exists verbatim in the
    # registry must never resolve to a look-alike row — 2ISWZ321 and
    # 2ISW2321 are BOTH real TIDs, so confusables (0↔O, 1↔I, Z↔2, …) are a
    # fallback for OCR-style typos, not a licence to pick a different real
    # merchant.
    for r in rows:
        for field in RESOLVE_COLS:
            key = _norm(r[field])
            if not key:
                continue
            for v in values:
                vu = v.upper().strip()
                if vu in out:
                    continue
                if key == vu:
                    out[vu] = r
    # Pass 2: confusable matches only for values with no exact row
    # ('21030265' -> the '2103O265' row when the registry stores letters).
    for r in rows:
        for field in RESOLVE_COLS:
            key = _norm(r[field])
            if not key:
                continue
            for v in values:
                vu = v.upper().strip()
                if vu in out:
                    continue
                if confusable_key(key) == confusable_key(vu):
                    out[vu] = r
    return out


def static_accounts_for_acc(conn, accs: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Static-account terminal rows by static account / account number."""
    out: Dict[str, List[Dict[str, Any]]] = {a.upper(): [] for a in accs}
    if not accs:
        return out
    expanded = _expand_confusables(accs)
    q = ",".join("?" for _ in expanded)
    rows = _fetch(
        conn,
        f"SELECT mxcode, merchant_name, static_acc_no, payable_code, alias, "
        f"account_name, account_number, tid, sheet_name FROM merchants "
        f"WHERE (UPPER(TRIM(static_acc_no)) IN ({q}) "
        f"OR UPPER(TRIM(account_number)) IN ({q})) AND "
        f"(static_acc_no IS NOT NULL AND TRIM(static_acc_no) != '')",
        expanded + expanded,
    )
    # Key each row under BOTH static_acc_no and account_number so a pasted
    # value of either kind finds its row (the SQL matched on either column).
    for r in rows:
        for key in (_norm(r["static_acc_no"]), _norm(r["account_number"])):
            if key and key in out:
                out[key].append(r)
    return out


def static_accounts_for_mx(conn, mxcodes: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Static-account terminal rows per MX code (beneficiary in merchant_name,
    account number in static_acc_no, bank in account_name)."""
    out: Dict[str, List[Dict[str, Any]]] = {m.upper(): [] for m in mxcodes}
    if not mxcodes:
        return out
    expanded = _expand_confusables(mxcodes)
    q = ",".join("?" for _ in expanded)
    rows = _fetch(
        conn,
        f"SELECT mxcode, merchant_name, static_acc_no, payable_code, alias, "
        f"account_name, tid, sheet_name FROM merchants "
        f"WHERE UPPER(TRIM(mxcode)) IN ({q}) AND "
        f"(static_acc_no IS NOT NULL AND TRIM(static_acc_no) != '')",
        [m.upper().strip() for m in expanded],
    )
    for r in rows:
        key = _norm(r["mxcode"])
        if key in out:
            out[key].append(r)
    return out


def static_rows_for_tid(conn, tids: List[str]) -> Dict[str, Dict[str, Any]]:
    """First static-account-manager (QTB) terminal row per TID.

    The QTB source sheets ('static_account_terminal...') carry the per-
    terminal payable/alias/static account the business relies on. Field
    pipelines that resolve a TID to one registry row must prefer this row
    so alias/payable values come from the terminal's own QTB entry — the
    parameter file's first-row values are a different set (MEDPLUS:
    QTB alias 022962 vs parameter-file alias 006793 for the same TID).
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not tids:
        return out
    expanded = _expand_confusables(tids)
    q = ",".join("?" for _ in expanded)
    rows = _fetch(
        conn,
        f"SELECT tid, mxcode, merchant_name, payable_code, alias, static_acc_no, "
        f"account_name, sheet_name FROM merchants "
        f"WHERE UPPER(TRIM(tid)) IN ({q}) "
        f"AND LOWER(COALESCE(sheet_name, '')) LIKE '%static_account_terminal%'",
        [i.upper().strip() for i in expanded],
    )
    for r in rows:
        key = _norm(r["tid"])
        if key and key not in out:
            out[key] = r
    return out


# ── Cross-identifier family fallback ─────────────────────────────────────

# Fields eligible for the cross-identifier fallback: merchant-level contact
# attributes that are valid on ANY sibling row of the same merchant.
# Deliberately excludes financial fields (static account, payable, alias,
# account_number) — those are terminal-specific and must never be pulled
# from a sibling terminal's row.
CONTACT_FIELDS = ("email", "phone", "contact_name")


def family_rows_for(conn, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sibling registry rows for the same merchant as `row`.

    Two rows belong to the same merchant family when they share an exact
    merchant_name, merchant_id, TID or MX code — the same merchant is
    routinely keyed by a different identifier in each source (BIDWILL
    MX183515 on the static-account sheet, MX184740 on the NNPC sheet, both
    TID 2103O084). Used to harvest contact fields (email/phone) that live
    on a sibling row when the resolved row's own sheet has none.

    GUARD: an identifier link is only followed when it is unambiguous. A
    key shared with ANY other distinct merchant name (one MX registered to
    two merchants — MX184740 maps to BIDWILL *and* Banigo E.) links only
    rows whose names are token-compatible (>= 0.80), so a field never
    leaks across unrelated merchants through a shared identifier.
    """
    rname = _norm(row.get("merchant_name"))
    keys = {k: _norm(row.get(k)) for k in ("merchant_id", "tid", "mxcode")}
    keys = {k: v for k, v in keys.items() if v}
    if not rname and not keys:
        return []
    cols = ("id, merchant_name, tid, mxcode, phone, email, contact_name, "
            "sheet_name")
    sibs: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(rows):
        for s in rows:
            if row.get("id") is not None and s.get("id") == row.get("id"):
                continue
            if s.get("id") in seen:
                continue
            seen.add(s.get("id"))
            sibs.append(s)

    if rname:
        _add(_fetch(
            conn,
            f"SELECT {cols} FROM merchants "
            f"WHERE UPPER(TRIM(merchant_name)) = ?",
            [rname],
        ))
    for k, kv in keys.items():
        krows = _fetch(
            conn,
            f"SELECT {cols} FROM merchants WHERE UPPER(TRIM({k})) = ?",
            [kv],
        )
        names = {_norm(r.get("merchant_name")) for r in krows}
        names.discard("")
        others = {n for n in names if n != rname}
        if rname and others:
            # Key shared with at least one other distinct name — keep only
            # the rows whose name is this merchant's own name or a close
            # variant (branch spellings like the '- NNPC' suffix), never a
            # different merchant. This fires even when exactly TWO merchants
            # share the key (the real BIDWILL/Banigo case): one foreign
            # name is already enough to make the link ambiguous.
            krows = [
                r for r in krows
                if _norm(r.get("merchant_name")) == rname
                or token_sort_ratio(rname, _norm(r.get("merchant_name")))
                >= 0.8
            ]
        elif not rname and len(names) > 1:
            # Nameless row (some registry sheets carry identifiers only):
            # the link is only trustworthy when every named row under this
            # key belongs to ONE merchant. Two distinct names on one key
            # means we cannot tell which merchant the nameless row is —
            # harvest nothing (conservative).
            krows = []
        _add(krows)
    return sibs


def cross_identifier_field(conn, row: Dict[str, Any], field: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Harvest `field` from family rows when `row` itself has none.

    Contact fields (email/phone/contact_name) belong to the MERCHANT, not
    the individual terminal row, so a missing value on the resolved row's
    sheet can legitimately come from a sibling source row. Multiple values
    are joined with '; ' (the multi-contact display rule — several emails
    in one cell). Returns ("", []) when the row already has the field or
    no sibling carries one, so callers fall through unchanged.
    """
    if str(row.get(field) or "").strip():
        return "", []
    vals: List[str] = []
    sources: List[Dict[str, Any]] = []
    for s in family_rows_for(conn, row):
        v = str(s.get(field) or "").strip()
        if v and v.upper() not in [x.upper() for x in vals]:
            vals.append(v)
            sources.append(s)
    return ("; ".join(vals) if vals else ""), sources


def _name_status(user_name: str, registry_name: str) -> str:
    """Compare the user-provided pasted name with the registry name.

    Returns 'found' (match), 'name_mismatch' (both present but differ), or
    'no_name' (no user name to compare). Feature #7.
    """
    u, r = (user_name or "").strip(), (registry_name or "").strip()
    if not u:
        return "no_name"
    if not r:
        return "name_mismatch"  # registry has no name but user gave one
    sim = token_sort_ratio(u, r)
    return "found" if sim >= 0.55 else "name_mismatch"


def _name_for(named: List[Dict[str, str]], ident: str) -> str:
    for n in named:
        if _norm(n["id"]) == _norm(ident):
            return n["name"]
    return ""


# ── Step pipelines ────────────────────────────────────────────────────────
