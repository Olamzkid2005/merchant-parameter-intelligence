"""
pipelines.py — Intent pipelines for the task engine.

Each _pipeline_* takes (conn, task) and returns a render-ready table (rows +
columns + summary + not_found) for one intent; _PIPELINES maps intent ->
pipeline; _merge_tables merges compound-intent tables into one.
"""
import json
import logging
import re
from typing import Any, Dict, List, Tuple

from .db import (
    RESOLVE_COLS, _fetch, _name_for, _name_status, _norm, resolve_any,
    resolve_mx, static_accounts_for_acc, static_accounts_for_mx,
)
from .parser import key_merchant_matches, looks_like_address
from .vocab import (
    ADDRESS_LOCALITY_WORDS, ADDRESS_SOURCE_PRIORITY, ID_KINDS, NIGERIA_STATES,
)

logger = logging.getLogger(__name__)

def _resolve_name_rows(name: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Resolve a merchant NAME to registry rows via the search engine.

    Used by name-only requests ("get me all the information on medplus"):
    the name is run through the normal fuzzy search, and the best rows
    become the pipeline input, exactly as identifiers do.
    """
    from ..search import MerchantSearch
    searcher = MerchantSearch()
    try:
        out = []
        for r in searcher.search(name, limit=limit):
            d = dict(r.record)
            score = getattr(r, "score", None)
            if score is not None:
                d["_score"] = score
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("name resolution failed (%r): %s", name, exc)
        return []


def _address_source_rank(sheet_name: str) -> int:
    """Lower = preferred. The newest Medplus workbooks win when the same
    address exists in several files (the user's stated priority)."""
    sn = (sheet_name or "").upper()
    for i, pref in enumerate(ADDRESS_SOURCE_PRIORITY):
        if pref in sn:
            return i
    return len(ADDRESS_SOURCE_PRIORITY)


def _resolve_address_rows(conn, name: str,
                          limit: int = 4) -> List[Dict[str, Any]]:
    """Resolve a pasted ADDRESS to registry rows via the address column.

    Locality words (state/city: LAGOS, LEKKI, SANGOTEDO, …) are dropped from
    the query so a trailing ', LEKKI, LAGOS' doesn't collapse every Lagos
    row. Candidate rows come from a tiered fetch: AND on all landmarks (the
    tight, high-precision pass — 'PROVIDENCE PLAZA PLOT OLOKONLA' -> the
    PROVIDENCE row), then AND on the first three, then a wide OR fallback.
    An OR-only query with LIMIT is useless here — common landmarks like
    'WAY'/'PLOT' match tens of thousands of rows and a small window fills
    with single-token matches that never reach the scored set.

    The leading merchant-family prefix ('MEDPLUS OASIS CENTER…' stores
    'OASIS CENTER…') is dropped from the landmarks because it lives in
    merchant_name, not the address column.

    Surviving rows must overlap the query strongly (token-set ratio >= 0.5
    with at least two real tokens), then rank by overlap and source
    priority (Medplus files win). Returns [] when nothing matches — never a
    fuzzy name fallback, so an address absent from the registry honestly
    reports NOT FOUND.
    """
    toks = re.findall(r"[A-Z0-9]+", (name or "").upper())
    query = [w for w in toks if w not in ADDRESS_LOCALITY_WORDS and len(w) >= 3]
    if len(query) < 2:
        return []
    landmarks = [w for w in query if len(w) >= 4][:6]
    if not landmarks:
        return []
    # Drop a leading key-merchant root (MEDPLUS/ADDIDE/…) from the landmark
    # set — stored addresses rarely repeat it ('OASIS CENTER…' not 'MEDPLUS
    # OASIS CENTER…'), so an AND on it would reject the real row. Only when
    # the trimmed set keeps >= 2 landmarks: 'MEDPLUS MARINA' needs MEDPLUS
    # to stay (the Change sheet stores 'MEDPLUS MARINA LAGOS ISLAND').
    if key_merchant_matches(query[0]) and len(landmarks) >= 2:
        trimmed = [w for w in landmarks if w != query[0]]
        if len(trimmed) >= 2:
            landmarks = trimmed
    qset = set(query)
    sel = ("SELECT id, merchant_name, tid, mxcode, phone, email, address, "
           "sheet_name FROM merchants WHERE ")
    rows = []
    for tier in (landmarks, landmarks[:3]):
        if len(tier) < 2:
            continue
        conds = " AND ".join("UPPER(COALESCE(address,'')) LIKE ?"
                             for _ in tier)
        rows = _fetch(conn, sel + conds + " LIMIT 200",
                      [f"%{w}%" for w in tier])
        if len(rows) >= 1:
            break
    if not rows:
        # Wide OR fallback for reworded addresses (AND tiers found nothing).
        conds = " OR ".join("UPPER(COALESCE(address,'')) LIKE ?"
                            for _ in landmarks)
        rows = _fetch(conn, sel + conds + " LIMIT 2000",
                      [f"%{w}%" for w in landmarks])
    scored = []
    for r in rows:
        addr = set(re.findall(r"[A-Z0-9]+", (r.get("address") or "").upper()))
        inter = len(qset & addr)
        if inter < 2:
            continue
        ratio = inter / len(qset | addr)
        # 0.4 (not 0.5): a short query like 'MEDPLUS MARINA' vs the stored
        # 'MEDPLUS MARINA LAGOS ISLAND LAGOS STATE' scores 0.4 but is a
        # perfect match — both query tokens are in the address. inter >= 2
        # is the real precision gate for tiny queries.
        if ratio < 0.4:
            continue
        scored.append((ratio, inter, r))
    scored.sort(key=lambda t: (-t[0], -t[1],
                               _address_source_rank(t[2].get("sheet_name", ""))))
    out: List[Dict[str, Any]] = []
    seen = set()
    for _ratio, _inter, r in scored:
        key = (r.get("tid") or "") or r.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def _pipeline_static_account(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """TIDs/MX codes -> static account + beneficiary table (with name checks)."""
    idents = task["identifiers"]
    tids = idents.get("tid", [])
    mxs = idents.get("mxcode", [])
    named = task.get("named", [])
    if not tids and not mxs and not (idents.get("static") or idents.get("account")) \
            and not (task.get("names") or []):
        # Nothing to resolve — a template with no merchant filled in. Say so
        # plainly instead of returning an empty table.
        return {
            "intent": "static_account",
            "pipeline": ["resolve_mx", "static_account"],
            "summary": "No merchant identifier or name found in the request - "
                       "add a TID, MX code, or merchant name.",
            "columns": ["Identifier", "TID", "Merchant", "MX Code",
                        "Static Account Number", "Beneficiary", "Payable Code",
                        "Alias", "Bank", "Status"],
            "rows": [], "not_found": [],
        }

    rows: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    seen = set()

    # Static account numbers / account numbers given directly -> their rows.
    direct_accs = list(dict.fromkeys(idents.get("static", []) + idents.get("account", [])))
    acc_map = static_accounts_for_acc(conn, direct_accs)

    mx_by_tid = resolve_mx(conn, tids)
    given_mx = {m.upper().strip() for m in mxs}
    static_map = static_accounts_for_mx(
        conn, list({m["mxcode"] for m in mx_by_tid.values() if m.get("mxcode")} | given_mx)
    )

    def emit(tid_val, mx_val, merchant_name, static, identifier):
        key = (tid_val or "", mx_val or "", static.get("static_acc_no", ""))
        if key in seen:
            return
        seen.add(key)
        status = "found" if static.get("static_acc_no") else "no_static_account"
        ns = _name_status(_name_for(named, identifier), merchant_name or "")
        if status == "found" and ns == "name_mismatch":
            status = "name_mismatch"
        elif status == "no_static_account" and ns == "name_mismatch":
            status = "name_mismatch"
        rows.append({
            "identifier": identifier or "",
            "tid": tid_val or "",
            "merchant": merchant_name or "",
            "mxcode": mx_val or "",
            "static_acc_no": static.get("static_acc_no") or "",
            "beneficiary": static.get("merchant_name") or merchant_name or "",
            "payable_code": static.get("payable_code") or "",
            "alias": static.get("alias") or "",
            "bank": static.get("account_name") or "",
            "status": status,
        })

    for tid in tids:
        row = mx_by_tid.get(tid.upper().strip())
        if not row or not row.get("mxcode"):
            not_found.append({"id": tid, "kind": "tid", "reason": "TID not in registry"})
            continue
        mx = _norm(row["mxcode"])
        statics = static_map.get(mx, [])
        if not statics:
            emit(tid, row["mxcode"], row["merchant_name"], {}, tid)
            continue
        for s in statics:
            emit(tid, row["mxcode"], row["merchant_name"], s, tid)
    for mx in mxs:
        statics = static_map.get(mx.upper().strip(), [])
        if not statics:
            not_found.append({"id": mx, "kind": "mxcode", "reason": "no static account row"})
            continue
        for s in statics:
            emit("", mx, s.get("merchant_name"), s, mx)
    # Direct static/account numbers -> their static terminal rows
    for acc in direct_accs:
        statics = acc_map.get(acc.upper().strip(), [])
        if not statics:
            not_found.append({"id": acc, "kind": "account",
                              "reason": "no static terminal row for this account number"})
            continue
        for s in statics:
            emit("", s.get("mxcode") or "", s.get("merchant_name"), s, acc)

    # Name-only requests: resolve the name to rows, then pull static accounts
    # via each row's MX code ("get the static account for MEDPLUS"). The name-
    # resolved MX codes are NOT in static_map (built from the identifier input
    # only), so a lazy per-MX lookup is used for them.
    name_static_cache: Dict[str, List[Dict[str, Any]]] = {}

    def statics_for(mx: str) -> List[Dict[str, Any]]:
        if mx not in name_static_cache:
            name_static_cache[mx] = static_accounts_for_mx(conn, [mx]).get(mx, [])
        return name_static_cache[mx]

    for n in task.get("names") or []:
        name_rows = _resolve_name_rows(n)
        if not name_rows:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
            continue
        any_mx = False
        for rec in name_rows:
            mx = _norm(rec.get("mxcode"))
            if not mx:
                continue
            any_mx = True
            statics = static_map.get(mx) or statics_for(mx)
            if statics:
                for st in statics:
                    emit("", rec.get("mxcode") or "", rec.get("merchant_name") or "", st, n)
            else:
                emit("", rec.get("mxcode") or "", rec.get("merchant_name") or "", {}, n)
        if not any_mx:
            not_found.append({"id": n, "kind": "name", "reason": "no MX code on resolved rows"})

    resolved_kinds = []
    if tids:
        resolved_kinds.append(f"{len(tids)} terminal(s)")
    if mxs:
        resolved_kinds.append(f"{len(mxs)} MX code(s)")
    if direct_accs:
        resolved_kinds.append(f"{len(direct_accs)} account number(s)")
    for n in task.get("names") or []:
        resolved_kinds.append(f"name '{n}'")
    summary = (f"Resolved {' and '.join(resolved_kinds) or '0 identifiers'} -> "
               f"static accounts + beneficiaries ({len(rows)} row(s)).")

    return {
        "intent": "static_account",
        "pipeline": ["resolve_mx", "static_account"],
        "summary": summary,
        "columns": ["Identifier", "TID", "Merchant", "MX Code", "Static Account Number",
                    "Beneficiary", "Payable Code", "Alias", "Bank", "Status"],
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_field(conn, task, field: str, label: str, intent: str):
    """Generic pipeline: identifiers -> one registry field (email/phone/mx)."""
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    resolved = resolve_any(conn, values)
    named = task.get("named", [])
    rows, not_found = [], []
    for v in values:
        r = resolved.get(v.upper().strip())
        if not r:
            not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
            continue
        status = _name_status(_name_for(named, v), r.get("merchant_name") or "")
        rows.append({
            "identifier": v,
            "merchant": r.get("merchant_name") or "",
            label: r.get(field) or "",
            "tid": r.get("tid") or "",
            "mxcode": r.get("mxcode") or "",
            "sheet": r.get("sheet_name") or "",
            "status": status,
        })
    # Name-only requests ("get the email for THE FILM HOUSE"): resolve the
    # name through the search engine and pull the field per best row. When
    # the request's names are ADDRESSES ('get me the tids for BRITISH
    # INTERNATIONAL SCHOOL ROAD, LEKKI, LAGOS'), match against the address
    # column instead — fuzzy name search has no merchant-name overlap for a
    # road + city string and returns unrelated stores.
    by_address = False
    for n in task.get("names") or []:
        if task.get("names_are_addresses") and looks_like_address(n):
            name_rows = _resolve_address_rows(conn, n)
            if name_rows:
                by_address = True
            else:
                # Address absent from the address column — fall back to a
                # HIGH-confidence name match ONLY (merchants whose name reads
                # like an address, e.g. 'BOKKU MART- ILAJE AJAH'), never the
                # fuzzy tier. A genuinely missing address reports NOT FOUND.
                name_rows = [r for r in _resolve_name_rows(n)
                             if (r.get("_score") or 0) >= 8.5]
        else:
            name_rows = _resolve_name_rows(n)
        if not name_rows:
            not_found.append({
                "id": n,
                "kind": "address" if by_address else "name",
                "reason": ("address not in registry" if by_address
                            else "name not in registry"),
            })
            continue
        for rec in name_rows:
            status = ("address_match" if by_address
                      else _name_status(n, rec.get("merchant_name") or ""))
            rows.append({
                "identifier": n,
                "merchant": rec.get("merchant_name") or "",
                label: rec.get(field) or "",
                "tid": rec.get("tid") or "",
                "mxcode": rec.get("mxcode") or "",
                "sheet": rec.get("sheet_name") or "",
                "status": status,
                # Address matches carry the stored address so the user can
                # verify WHY this TID was chosen (the pasted line is already
                # in `identifier`).
                **({"address": rec.get("address") or ""} if by_address else {}),
            })
    src = f"{len(values)} identifier(s)" if values \
        else f"{len(task.get('names') or [])} name(s)"
    cols: List[str] = []
    base_cols = ["Identifier", "Merchant", label, "TID", "MX Code"]
    if by_address:
        base_cols.append("Matched Address")
    for c in base_cols + ["Source", "Status"]:
        if c not in cols:
            cols.append(c)
    return {
        "intent": intent,
        "pipeline": [f"resolve_{field}"],
        "summary": f"Pulled {label.lower()} for {len(rows)}/{src}.",
        "columns": cols,
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_tid(conn, task):
    return _pipeline_field(conn, task, "tid", "TID", "tid")


def _pipeline_email(conn, task):
    return _pipeline_field(conn, task, "email", "Email", "email")


def _pipeline_phone(conn, task):
    return _pipeline_field(conn, task, "phone", "Phone", "phone")


def _pipeline_mxcode(conn, task):
    return _pipeline_field(conn, task, "mxcode", "MX Code", "mxcode")


def _pipeline_address(conn, task):
    return _pipeline_field(conn, task, "address", "Address", "address")


def _pipeline_bank(conn, task):
    return _pipeline_field(conn, task, "bank", "Bank", "bank")


def _pipeline_account_name(conn, task):
    return _pipeline_field(conn, task, "account_name", "Account Name", "account_name")


def _pipeline_account_number(conn, task):
    return _pipeline_field(conn, task, "account_number", "Account Number", "account_number")


def _pipeline_payable(conn, task):
    return _pipeline_field(conn, task, "payable_code", "Payable Code", "payable")


def _pipeline_alias(conn, task):
    return _pipeline_field(conn, task, "alias", "Alias", "alias")


def _pipeline_contact(conn, task):
    return _pipeline_field(conn, task, "contact_name", "Contact", "contact")


def _pipeline_onboarded(conn, task):
    return _pipeline_field(conn, task, "onboarded_date", "Onboarded", "onboarded")


def _pipeline_state(conn, task):
    return _pipeline_field(conn, task, "state", "State", "state")


def _pipeline_source(conn, task):
    return _pipeline_field(conn, task, "sheet_name", "Source", "source")


def _pipeline_profile(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Identifiers -> full registry row (merchant, contacts, accounts, source)."""
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    resolved = resolve_any(conn, values)
    named = task.get("named", [])
    rows, not_found = [], []
    for v in values:
        r = resolved.get(v.upper().strip())
        if not r:
            not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
            continue
        status = _name_status(_name_for(named, v), r.get("merchant_name") or "")
        rows.append({
            "identifier": v,
            "merchant": r.get("merchant_name") or "",
            "tid": r.get("tid") or "",
            "mxcode": r.get("mxcode") or "",
            "phone": r.get("phone") or "",
            "email": r.get("email") or "",
            "contact": r.get("contact_name") or "",
            "address": r.get("address") or "",
            "account_name": r.get("account_name") or "",
            "account_number": r.get("account_number") or "",
            "bank": r.get("state") or "",
            "sheet": r.get("sheet_name") or "",
            "status": status,
        })
    # Name-only requests: "get me all the information on medplus" — resolve
    # the name to its best rows and return them as full profiles.
    for n in task.get("names") or []:
        name_rows = _resolve_name_rows(n)
        if not name_rows:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
            continue
        for rec in name_rows:
            status = _name_status(n, rec.get("merchant_name") or "")
            rows.append({
                "identifier": n,
                "merchant": rec.get("merchant_name") or "",
                "tid": rec.get("tid") or "",
                "mxcode": rec.get("mxcode") or "",
                "phone": rec.get("phone") or "",
                "email": rec.get("email") or "",
                "contact": rec.get("contact_name") or "",
                "address": rec.get("address") or "",
                "account_name": rec.get("account_name") or "",
                "account_number": rec.get("account_number") or "",
                "bank": rec.get("state") or "",
                "sheet": rec.get("sheet_name") or "",
                "status": status,
            })
    src = f"{len(values)} identifier(s)" if values \
        else f"{len(task.get('names') or [])} name(s)"
    return {
        "intent": "profile",
        "pipeline": ["resolve_full"],
        "summary": f"Full profiles for {len(rows)}/{src}.",
        "columns": ["Identifier", "Merchant", "TID", "MX Code", "Phone", "Email",
                    "Contact", "Address", "Account Name", "Account Number",
                    "State", "Source", "Status"],
        "rows": rows,
        "not_found": not_found,
    }


# Keys extracted from a Change-of-details row's raw_data JSON. The build maps
# OLD/NEW BANK ACC NO into account_number (NEW wins) and OLD/NEW BANK CODE into
# bank, but raw_data keeps EVERY raw column, so the before/after pairs are
# reconstructed from there when present.
CHANGE_RAW_FIELDS = [
    ("OLD BANK ACC NO", "Old Bank Acc No"),
    ("NEW BANK ACC NO", "New Bank Acc No"),
    ("OLD BANK CODE", "Old Bank Code"),
    ("NEW BANK CODE", "New Bank Code"),
    ("OLD PHYSICAL ADDR", "Old Address"),
    ("NEW PHYSICAL ADDR", "New Address"),
    ("OLD MERCHANT ACCOUNT NAME", "Old Account Name"),
    ("NEW MERCHANT ACCOUNT NAME", "New Account Name"),
    ("OLD ACCOUNT NO", "Old Account No"),
    ("NEW ACCOUNT NO", "New Account No"),
    ("NEW ALIAS", "New Alias"),
    ("OLD EMAIL", "Old Email"),
    ("NEW EMAIL", "New Email"),
    ("OLD BANK ACC", "Old Bank Acc"),
    ("NEW BANK ACC", "New Bank Acc"),
]


def _change_rows_for(conn, merchant_names: List[str], mxcodes: List[str],
                     tids: List[str], limit: int = 50) -> List[Dict[str, Any]]:
    """Rows from the 'Change of merchant details' sheet for a merchant.

    Matched by MX code, TID, or merchant-name equality (the sheet keeps its
    own copy of the merchant name, so an exact name match is the fallback).
    """
    conds: List[str] = []
    params: List[str] = []
    if mxcodes:
        q = ",".join("?" for _ in mxcodes)
        conds.append(f"UPPER(TRIM(mxcode)) IN ({q})")
        params += [m.upper().strip() for m in mxcodes]
    if tids:
        q = ",".join("?" for _ in tids)
        conds.append(f"UPPER(TRIM(tid)) IN ({q})")
        params += [t.upper().strip() for t in tids]
    if merchant_names:
        for n in merchant_names:
            conds.append("UPPER(TRIM(merchant_name)) = ?")
            params.append(n.upper().strip())
    if not conds:
        return []
    sql = (f"SELECT id, merchant_name, mxcode, payable_code, tid, terminal_serial, "
           f"slip_header, email, phone, address, account_name, account_number, "
           f"bank, raw_data, sheet_name, row_number FROM merchants "
           f"WHERE sheet_name LIKE '%Change%' AND ({' OR '.join(conds)}) "
           f"ORDER BY merchant_name, mxcode LIMIT ?")
    rows = _fetch(conn, sql, params + [limit])
    return rows


def _norm_header(key: str) -> str:
    """Normalise a raw header for matching: collapse all whitespace (headers
    carry varying padding, e.g. 'NEW  MERCHANT ACCOUNT NAME' vs
    'NEW MERCHANT ACCOUNT NAME'), strip nbsp, upper-case."""
    return re.sub(r"\s+", " ", str(key or "").replace("\xa0", " ")).strip().upper()


def _parse_change_pairs(row: Dict[str, Any]) -> Dict[str, str]:
    """Extract the OLD->NEW pairs from a Change row's raw_data JSON."""
    out: Dict[str, str] = {}
    try:
        raw = json.loads(row.get("raw_data") or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    # Find keys case-insensitively and whitespace-tolerant (JSON preserves the
    # raw header casing/padding).
    upper_map: Dict[str, str] = {}
    for k, v in raw.items():
        upper_map[_norm_header(k)] = str(v or "")
    for raw_key, label in CHANGE_RAW_FIELDS:
        out[label] = upper_map.get(_norm_header(raw_key), "")
    return out


def _pipeline_change_details(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """'Change of account details' — the before/after rows for a merchant.

    Resolves identifiers or merchant names, then pulls every row from the
    Change-of-merchant-details sheet tied to that merchant (by MX/TID/name)
    and reconstructs the OLD->NEW account/bank/address pairs.
    """
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    names = task.get("names") or []

    mxcodes: List[str] = list(dict.fromkeys(idents.get("mxcode", [])))
    tids: List[str] = list(dict.fromkeys(idents.get("tid", [])))

    resolved_names: List[str] = []
    not_found: List[Dict[str, Any]] = []

    # Identifier input: resolve to MX/TID/name so the Change-sheet lookup
    # has every handle to match on.
    if values:
        resolved = resolve_any(conn, values)
        for v in values:
            r = resolved.get(v.upper().strip())
            if not r:
                not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
                continue
            mx = _norm(r.get("mxcode"))
            tid = _norm(r.get("tid"))
            if mx:
                mxcodes.append(mx)
            if tid:
                tids.append(tid)
            if r.get("merchant_name"):
                resolved_names.append(str(r["merchant_name"]).strip())

    # Name input: resolve via the search engine first (name may differ from
    # the Change sheet's own copy). Only the searched NAME is used for exact
    # name matching, and only MX codes from rows whose name actually matches
    # the query (plus the top result) are collected — fuzzy neighbours like
    # JUSTY & SON / JUSTIN BARDI must not pollute the match or not_found list.
    for n in names:
        key = _norm(n)
        name_rows = _resolve_name_rows(n)
        if not name_rows:
            # Still try exact-name match on the Change sheet itself.
            resolved_names.append(n)
            continue
        added_name = False
        for rank, rec in enumerate(name_rows):
            mx = _norm(rec.get("mxcode"))
            rname = _norm(rec.get("merchant_name"))
            if rank == 0 or rname == key:
                if mx and rname == key and not added_name:
                    resolved_names.append(str(rec["merchant_name"]).strip())
                    added_name = True
                if mx:
                    mxcodes.append(mx)
        if not added_name and key not in resolved_names:
            resolved_names.append(n)

    mxcodes = list(dict.fromkeys(mxcodes))
    tids = list(dict.fromkeys(tids))
    resolved_names = list(dict.fromkeys(resolved_names))

    change_rows = _change_rows_for(conn, resolved_names, mxcodes, tids)

    # Resolved names/ids that produced NO Change-sheet rows get an explicit
    # not_found entry so the UI says "no change-of-details records" instead of
    # silently returning an empty table.
    found_mx = {_norm(r.get("mxcode")) for r in change_rows}
    found_names = {_norm(r.get("merchant_name")) for r in change_rows}
    for n in resolved_names:
        key = _norm(n)
        if key not in found_names and not any(
                _norm(x.get("id")) == key for x in not_found):
            not_found.append({"id": n, "kind": "name",
                              "reason": "no change-of-details records in registry"})
    for mx in mxcodes:
        key = _norm(mx)
        if key and key not in found_mx and not any(
                _norm(x.get("id")) == key for x in not_found):
            not_found.append({"id": mx, "kind": "mxcode",
                              "reason": "no change-of-details records in registry"})

    rows: List[Dict[str, Any]] = []
    seen = set()
    for r in change_rows:
        key = (r.get("mxcode") or "", r.get("tid") or "", r.get("row_number") or 0)
        if key in seen:
            continue
        seen.add(key)
        pairs = _parse_change_pairs(r)
        has_change = any(v for v in pairs.values())
        rows.append({
            "merchant": r.get("merchant_name") or "",
            "mxcode": r.get("mxcode") or "",
            "payable": r.get("payable_code") or "",
            "tid": r.get("tid") or "",
            "email": r.get("email") or "",
            "phone": r.get("phone") or "",
            "slip_header": r.get("slip_header") or "",
            "current_acc": r.get("account_number") or "",
            "current_bank": r.get("bank") or "",
            "current_addr": r.get("address") or "",
            "current_acct_name": r.get("account_name") or "",
            **pairs,
            "change_detected": has_change,
            "sheet": r.get("sheet_name") or "",
            "row": r.get("row_number") or "",
        })

    src = []
    if values:
        src.append(f"{len(values)} identifier(s)")
    if names:
        src.append(f"{len(names)} name(s)")
    return {
        "intent": "change_details",
        "pipeline": ["resolve", "change_sheet"],
        "summary": (f"Change of account/merchant details for "
                    f"{len(rows)} row(s) from {' and '.join(src) or 'registry'}."),
        "columns": ["Merchant", "MX Code", "Payable", "TID", "Email", "Phone",
                    "Slip Header", "Current Account", "Current Bank",
                    "Old Bank Acc No", "New Bank Acc No", "Old Bank Code",
                    "New Bank Code", "Old Address", "New Address",
                    "Old Account Name", "New Account Name", "Changed",
                    "Source", "Row"],
        "rows": rows,
        "not_found": not_found,
    }


SEGMENT_COLUMNS = [
    "Merchant", "TID", "MX Code", "Phone", "Email", "Address",
    "Contact", "Account Name", "Account Number", "Bank", "State",
    "Onboarded", "Source", "Row",
]

# Columns a segment fragment is searched against.
SEGMENT_SEARCH_COLS = ("merchant_name", "slip_header", "account_name",
                       "contact_name", "sheet_name")


def _segment_where(segment: str) -> Tuple[str, List[str]]:
    """WHERE fragment + params matching a segment across the searchable cols.

    Multi-token segments use token-AND ('NNPC PARAMETER BATCH' matches sheet
    'NNPC PARAMETER FILE BATCH'); single tokens match any column.
    """
    tokens = [t for t in segment.split() if t][:4]
    col_cond = " OR ".join(
        f"UPPER(COALESCE({c},'')) LIKE ?" for c in SEGMENT_SEARCH_COLS)
    if len(tokens) > 1:
        conds = " AND ".join(f"({col_cond})" for _ in tokens)
        params: List[str] = [f"%{t}%" for t in tokens for _ in SEGMENT_SEARCH_COLS]
    else:
        conds = col_cond
        params = [f"%{tokens[0] if tokens else segment}%"] * len(SEGMENT_SEARCH_COLS)
    return conds, params


def _append_filters(conds: str, params: List[str],
                    extra: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Append state + presence filters from a request's params.

    'in lagos' -> state=LAGOS (matches the state column by any alias);
    'with email' -> requires a non-empty email; 'without email' / 'no email'
    (coverage intent) -> requires an EMPTY email. Returns the new WHERE + params.
    """
    conds_list = [f"({conds})"] if conds else []
    state = extra.get("state")
    if state:
        aliases = NIGERIA_STATES.get(state, [state])
        q = ",".join("?" for _ in aliases)
        conds_list.append(f"UPPER(COALESCE(state,'')) IN ({q})")
        params += [a.upper() for a in aliases]
    for f in extra.get("has", []):
        col = {"email": "email", "phone": "phone", "address": "address"}.get(f)
        if col:
            conds_list.append(f"COALESCE({col},'') != ''")
    for f in extra.get("missing", []):
        col = {"email": "email", "phone": "phone", "address": "address"}.get(f)
        if col:
            conds_list.append(f"COALESCE({col},'') = ''")
    return " AND ".join(conds_list), params


def _pipeline_segment(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Collection/segment request: EVERY row matching a fragment, rich fields.

    'get me all the addresses of all nnpc stations' -> all rows whose
    merchant name / slip header / account name / contact / sheet contains
    'NNPC', returned with the full field set. Filters ("in lagos", "with
    email", "top 20") from task['params'] narrow the result.
    """
    segment = (task.get("segment") or "").strip()
    fields = task.get("segment_fields") or []
    if not segment:
        return {
            "intent": "segment",
            "pipeline": ["segment"],
            "summary": "No segment found in the request - try 'get me all the "
                       "addresses of all nnpc stations'.",
            "columns": [], "rows": [], "not_found": [],
        }
    conds, params = _segment_where(segment)
    limit = (task.get("params") or {}).get("limit") or 1000
    conds, params = _append_filters(conds, params, task.get("params") or {})
    sel = ("SELECT merchant_name, tid, mxcode, phone, email, address, "
           "contact_name, account_name, account_number, bank, state, "
           "onboarded_date, sheet_name, row_number FROM merchants "
           f"WHERE {conds} ORDER BY merchant_name, tid LIMIT {limit}")
    rows = _fetch(conn, sel, params)
    if not rows and len([t for t in segment.split() if t]) > 1:
        # Fallback: the joined phrase as one LIKE (spacing may differ).
        like = f"%{segment}%"
        conds, params = _append_filters(
            " OR ".join(f"UPPER(COALESCE({c},'')) LIKE ?" for c in SEGMENT_SEARCH_COLS),
            [like] * len(SEGMENT_SEARCH_COLS), task.get("params") or {})
        rows = _fetch(
            conn,
            "SELECT merchant_name, tid, mxcode, phone, email, address, "
            "contact_name, account_name, account_number, bank, state, "
            "onboarded_date, sheet_name, row_number FROM merchants "
            f"WHERE {conds} ORDER BY merchant_name, tid LIMIT {limit}",
            params,
        )
    out = [{
        "merchant": r.get("merchant_name") or "",
        "tid": r.get("tid") or "",
        "mxcode": r.get("mxcode") or "",
        "phone": r.get("phone") or "",
        "email": r.get("email") or "",
        "address": r.get("address") or "",
        "contact": r.get("contact_name") or "",
        "account_name": r.get("account_name") or "",
        "account_number": r.get("account_number") or "",
        "bank": r.get("bank") or "",
        "state": r.get("state") or "",
        "onboarded": (r.get("onboarded_date") or "")[:10],
        "source": r.get("sheet_name") or "",
        "row": r.get("row_number") or "",
    } for r in rows]
    label = " + ".join(fields) if fields else "all fields"
    truncated = ""
    if len(out) >= 1000:
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM merchants WHERE {conds}", params).fetchone()[0]
            if total > len(out):
                truncated = f" (showing first {len(out)} of {total})"
        except Exception:
            pass
    summary = (f"Segment '{segment}': {len(out)} row(s){truncated} - {label}."
               if out else f"Segment '{segment}': no rows matched.")
    return {
        "intent": "segment",
        "pipeline": ["segment"],
        "summary": summary,
        "columns": SEGMENT_COLUMNS,
        "rows": out,
        "not_found": [] if out else [{"id": segment, "kind": "segment",
                                       "reason": "no rows matched"}],
    }


def _count_linked(conn, r: Dict[str, Any]) -> int:
    """How many registry rows share ANY identifier with the resolved row r."""
    handles = [str(r.get(c)).strip() for c in RESOLVE_COLS if r.get(c)]
    if not handles:
        return 1
    q = ",".join("?" for _ in handles)
    where = " OR ".join(f"UPPER(TRIM({c})) IN ({q})" for c in RESOLVE_COLS)
    return conn.execute(
        f"SELECT COUNT(*) FROM merchants WHERE {where}",
        handles * len(RESOLVE_COLS)).fetchone()[0]


def _pipeline_count(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Count intent: how many rows match a segment / name / identifiers.

    'how many nnpc merchants' -> one row 'Rows matching NNPC' -> N.
    'how many terminals does LAGOON WATERS have' -> rows per linked handle.
    """
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    rows_out: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    if values:
        resolved = resolve_any(conn, values)
        for v in values:
            r = resolved.get(v.upper().strip())
            if not r:
                not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
                continue
            rows_out.append({"metric": f"Records linked to {v}",
                             "count": _count_linked(conn, r)})
    for n in task.get("names") or []:
        name_rows = _resolve_name_rows(n)
        rows_out.append({"metric": f"Records matching '{n}'",
                         "count": len(name_rows)})
        if not name_rows:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
    seg = (task.get("segment") or "").strip()
    if seg:
        conds, params = _segment_where(seg)
        conds, params = _append_filters(conds, params, task.get("params") or {})
        n = conn.execute(
            f"SELECT COUNT(*) FROM merchants WHERE {conds}", params).fetchone()[0]
        rows_out.append({"metric": f"Rows matching '{seg}'", "count": n})
    if not rows_out:
        rows_out.append({"metric": "No countable target found", "count": 0})
    total = sum(r["count"] for r in rows_out)
    return {
        "intent": "count",
        "pipeline": ["count"],
        "summary": f"Counted {len(rows_out)} metric(s), total {total}.",
        "columns": ["Metric", "Count"],
        "rows": rows_out,
        "not_found": not_found,
    }


def _scoped_row_ids(conn, task: Dict[str, Any]) -> List[str]:
    """Registry row ids the task's identifiers resolve to ('' when none)."""
    values = [v for k in ID_KINDS for v in (task.get("identifiers") or {}).get(k, [])]
    if not values:
        return []
    resolved = resolve_any(conn, values)
    ids = []
    for v in values:
        r = resolved.get(v.upper().strip())
        if r and str(r.get("id")) not in ids:
            ids.append(str(r["id"]))
    return ids


def _pipeline_duplicates(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Duplicate intent: merchant names appearing in more than one row.

    Groups by the upper-cased merchant name and lists groups with >1 member,
    optionally limited to rows matching a segment ("duplicates in the NNPC
    file") or to the rows an identifier resolves to ("find duplicates for
    MX183639").
    """
    seg = (task.get("segment") or "").strip()
    conds_list = []
    params: List[str] = []
    if seg:
        conds, params = _segment_where(seg)
        conds, params = _append_filters(conds, params, task.get("params") or {})
        if conds:
            conds_list.append(f"({conds})")
    ids = _scoped_row_ids(conn, task)
    if ids:
        q = ",".join("?" for _ in ids)
        conds_list.append(f"id IN ({q})")
        params += ids
    conds_list.append("merchant_name IS NOT NULL AND TRIM(merchant_name) != ''")
    where = " AND ".join(conds_list)
    rows = _fetch(
        conn,
        "SELECT MAX(merchant_name) AS name, COUNT(*) AS n, "
        "GROUP_CONCAT(DISTINCT sheet_name) AS sources "
        f"FROM merchants WHERE {where} "
        "GROUP BY UPPER(TRIM(merchant_name)) HAVING n > 1 "
        "ORDER BY n DESC, name LIMIT 500",
        params,
    )
    out = [{
        "merchant": r.get("name") or "",
        "rows": r.get("n") or 0,
        "sources": (r.get("sources") or "")[:200],
    } for r in rows]
    return {
        "intent": "duplicates",
        "pipeline": ["duplicates"],
        "summary": f"{len(out)} merchant name(s) appear more than once.",
        "columns": ["Merchant Name", "Rows", "Sources"],
        "rows": out,
        "not_found": [] if out else [{"id": seg or "registry",
                                       "kind": "duplicates",
                                       "reason": "no duplicate merchant names"}],
    }


def _pipeline_summary(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Summary intent: aggregate metrics for a segment (or whole registry).

    'summarize the NNPC file' -> total rows, distinct names, coverage of
    email / phone / address / MX / TID, distinct states.
    """
    seg = (task.get("segment") or "").strip()
    conds, params = "", []
    if seg:
        conds, params = _segment_where(seg)
        conds, params = _append_filters(conds, params, task.get("params") or {})
    ids = _scoped_row_ids(conn, task)
    if ids:
        q = ",".join("?" for _ in ids)
        conds = f"({conds})" + " AND " if conds else ""
        conds += f"id IN ({q})"
        params += ids
    where = f"WHERE {conds}" if conds else ""

    def scalar(sql: str) -> int:
        return conn.execute(sql, params).fetchone()[0]

    total = scalar(f"SELECT COUNT(*) FROM merchants {where}")
    name_cols = ["email", "phone", "address", "mxcode", "tid"]
    rows_out = [{"metric": "Total rows", "value": total},
                {"metric": "Distinct merchant names",
                 "value": scalar(f"SELECT COUNT(DISTINCT UPPER(TRIM(merchant_name))) "
                                 f"FROM merchants {where}")}]
    for c in name_cols:
        label = {"email": "With email", "phone": "With phone",
                 "address": "With address", "mxcode": "With MX code",
                 "tid": "With TID"}[c]
        rows_out.append({"metric": label, "value": scalar(
            f"SELECT COUNT(*) FROM merchants {where} "
            f"AND COALESCE({c},'') != ''")})
    rows_out.append({"metric": "Distinct states", "value": scalar(
        f"SELECT COUNT(DISTINCT state) FROM merchants {where} "
        "AND state IS NOT NULL AND TRIM(state) != ''")})
    return {
        "intent": "summary",
        "pipeline": ["summary"],
        "summary": f"Summary for '{seg}': {total} row(s)." if seg else \
                   f"Registry summary: {total} row(s).",
        "columns": ["Metric", "Value"],
        "rows": rows_out,
        "not_found": [] if total else [{"id": seg or "registry",
                                         "kind": "summary",
                                         "reason": "no rows to summarise"}],
    }


def _pipeline_beneficiary(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """TIDs/MX codes/account numbers -> beneficiary (static acct manager)."""
    idents = task["identifiers"]
    tids = idents.get("tid", [])
    mxs = idents.get("mxcode", [])
    direct_accs = list(dict.fromkeys(idents.get("static", []) + idents.get("account", [])))
    named = task.get("named", [])
    rows, not_found = [], []
    seen = set()

    mx_by_tid = resolve_mx(conn, tids)
    given_mx = {m.upper().strip() for m in mxs}
    static_map = static_accounts_for_mx(
        conn, list({m["mxcode"] for m in mx_by_tid.values() if m.get("mxcode")} | given_mx))
    acc_map = static_accounts_for_acc(conn, direct_accs)

    name_recs, name_mxs = [], set()
    for n in task.get("names") or []:
        recs = _resolve_name_rows(n)
        if not recs:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
            continue
        name_recs.append((n, recs))
        for rec in recs:
            mx = _norm(rec.get("mxcode"))
            if mx:
                name_mxs.add(mx)
    name_static = static_accounts_for_mx(conn, sorted(name_mxs)) if name_mxs else {}

    def emit(identifier, tid_val, mx_val, merchant, st):
        key = (tid_val or "", mx_val or "", (st or {}).get("static_acc_no", ""))
        if key in seen:
            return
        seen.add(key)
        status = "found" if (st or {}).get("static_acc_no") else "no_static_account"
        ns = _name_status(_name_for(named, identifier), merchant or "")
        if ns == "name_mismatch":
            status = "name_mismatch"
        rows.append({
            "identifier": identifier or "",
            "tid": tid_val or "",
            "mxcode": mx_val or "",
            "merchant": merchant or "",
            "beneficiary": (st or {}).get("merchant_name") or merchant or "",
            "static_acc_no": (st or {}).get("static_acc_no") or "",
            "bank": (st or {}).get("account_name") or "",
            "sheet": (st or {}).get("sheet_name") or "",
            "status": status,
        })

    for tid in tids:
        row = mx_by_tid.get(tid.upper().strip())
        if not row or not row.get("mxcode"):
            not_found.append({"id": tid, "kind": "tid", "reason": "TID not in registry"})
            continue
        mx = _norm(row["mxcode"])
        statics = static_map.get(mx, [])
        if not statics:
            emit(tid, row["mxcode"], row["merchant_name"], {}, tid)
            continue
        for s in statics:
            emit(tid, row["mxcode"], row["merchant_name"], s, tid)
    for mx in mxs:
        statics = static_map.get(mx.upper().strip(), [])
        if not statics:
            not_found.append({"id": mx, "kind": "mxcode", "reason": "no static account row"})
            continue
        for s in statics:
            emit(mx, "", mx, s.get("merchant_name"), s)
    for acc in direct_accs:
        statics = acc_map.get(acc.upper().strip(), [])
        if not statics:
            not_found.append({"id": acc, "kind": "account",
                              "reason": "no static terminal row for this account number"})
            continue
        for s in statics:
            emit(acc, "", s.get("mxcode") or "", s.get("merchant_name"), s)
    for n, recs in name_recs:
        for rec in recs:
            mx = _norm(rec.get("mxcode"))
            for st in name_static.get(mx, []):
                emit(n, rec.get("tid") or "", rec.get("mxcode") or "",
                     rec.get("merchant_name") or "", st)

    n_ids = len(tids) + len(mxs) + len(direct_accs)
    src = f"{n_ids} identifier(s)" if n_ids else f"{len(name_recs)} name(s)"
    return {
        "intent": "beneficiary",
        "pipeline": ["resolve_mx", "beneficiary"],
        "summary": f"Beneficiaries for {len(rows)} row(s) from {src or 'registry'}.",
        "columns": ["Identifier", "TID", "Merchant", "MX Code", "Beneficiary",
                    "Static Account No", "Bank", "Source", "Status"],
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_related(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Every registry record sharing ANY identifier with the input rows."""
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    resolved = resolve_any(conn, values)
    rows, not_found = [], []
    seen_ids = set()

    def linked_from(label, r):
        handles = [str(r[c]).strip() for c in RESOLVE_COLS
                   if str(r.get(c) or "").strip()]
        if not handles:
            not_found.append({"id": label, "kind": "any",
                              "reason": "no shared identifiers to trace"})
            return
        q = ",".join("?" for _ in handles)
        where = " OR ".join(f"UPPER(TRIM({c})) IN ({q})" for c in RESOLVE_COLS)
        linked = _fetch(
            conn,
            "SELECT id, merchant_name, tid, mxcode, phone, email, address, "
            f"sheet_name FROM merchants WHERE {where} "
            "ORDER BY merchant_name LIMIT 200",
            handles * len(RESOLVE_COLS))
        for lr in linked:
            if lr["id"] in seen_ids:
                continue
            seen_ids.add(lr["id"])
            rows.append({
                "identifier": label,
                "merchant": lr.get("merchant_name") or "",
                "tid": lr.get("tid") or "",
                "mxcode": lr.get("mxcode") or "",
                "phone": lr.get("phone") or "",
                "email": lr.get("email") or "",
                "address": lr.get("address") or "",
                "sheet": lr.get("sheet_name") or "",
                "status": "linked",
            })

    for v in values:
        r = resolved.get(v.upper().strip())
        if not r:
            not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
            continue
        linked_from(v, r)
    for n in task.get("names") or []:
        name_rows = _resolve_name_rows(n)
        if not name_rows:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
            continue
        for rec in name_rows:
            linked_from(n, rec)

    n_ids = len(values)
    src = f"{n_ids} identifier(s)" if n_ids else f"{len(task.get('names') or [])} name(s)"
    return {
        "intent": "related",
        "pipeline": ["find_related"],
        "summary": f"{len(rows)} registry record(s) share an identifier with {src}.",
        "columns": ["Input", "Merchant", "TID", "MX Code", "Phone", "Email",
                    "Address", "Source", "Status"],
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_formerly(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Name history: every name variant tied to the same MX/TID/merchant.

    Sources: the Change-of-merchant-details sheet (name swaps surface as
    OLD/NEW account-name pairs) and every registry row sharing the MX code.
    """
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    names = task.get("names") or []

    mxcodes = list(dict.fromkeys(idents.get("mxcode", [])))
    tids = list(dict.fromkeys(idents.get("tid", [])))
    resolved_names = []
    not_found = []

    if values:
        resolved = resolve_any(conn, values)
        for v in values:
            r = resolved.get(v.upper().strip())
            if not r:
                not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
                continue
            mx = _norm(r.get("mxcode"))
            tid = _norm(r.get("tid"))
            if mx:
                mxcodes.append(mx)
            if tid:
                tids.append(tid)
            if r.get("merchant_name"):
                resolved_names.append(str(r["merchant_name"]).strip())

    for n in names:
        key = _norm(n)
        name_rows = _resolve_name_rows(n)
        if not name_rows:
            resolved_names.append(n)
            continue
        for rank, rec in enumerate(name_rows):
            mx = _norm(rec.get("mxcode"))
            rname = _norm(rec.get("merchant_name"))
            if rank == 0 or rname == key:
                if mx:
                    mxcodes.append(mx)
                if rname == key:
                    resolved_names.append(str(rec["merchant_name"]).strip())
        if _norm(n) not in {_norm(x) for x in resolved_names}:
            resolved_names.append(n)

    mxcodes = list(dict.fromkeys(mxcodes))
    tids = list(dict.fromkeys(tids))

    variants, seen_variants = [], set()

    def add(name, source):
        key = (_norm(name), source)
        if key in seen_variants:
            return
        seen_variants.add(key)
        variants.append((str(name or "").strip(), source))

    change_rows = _change_rows_for(conn, resolved_names, mxcodes, tids, limit=200)
    for r in change_rows:
        if r.get("merchant_name"):
            add(r["merchant_name"], "Change-of-details sheet")
        pairs = _parse_change_pairs(r)
        for label in ("Old Account Name", "New Account Name"):
            if pairs.get(label):
                add(pairs[label], "Change-of-details sheet")

    if mxcodes:
        q = ",".join("?" for _ in mxcodes)
        reg = _fetch(
            conn,
            "SELECT DISTINCT merchant_name, sheet_name FROM merchants "
            f"WHERE UPPER(TRIM(mxcode)) IN ({q}) "
            "AND merchant_name IS NOT NULL AND TRIM(merchant_name) != '' "
            "ORDER BY merchant_name LIMIT 200",
            [m.upper().strip() for m in mxcodes])
        for r in reg:
            add(r["merchant_name"], r.get("sheet_name") or "registry")

    rows = [{"variant": nm, "source": src} for nm, src in variants]
    src = []
    if values:
        src.append(f"{len(values)} identifier(s)")
    if names:
        src.append(f"{len(names)} name(s)")
    return {
        "intent": "formerly",
        "pipeline": ["find_formerly"],
        "summary": (f"{len(rows)} name variant(s) found for "
                    f"{' and '.join(src) or 'registry'}."),
        "columns": ["Name Variant", "Found In"],
        "rows": rows,
        "not_found": not_found,
    }


_COMPARE_FIELDS = [
    ("merchant_name", "Merchant Name"),
    ("tid", "TID"),
    ("mxcode", "MX Code"),
    ("phone", "Phone"),
    ("email", "Email"),
    ("address", "Address"),
    ("account_name", "Account Name"),
    ("account_number", "Account Number"),
    ("bank", "Bank"),
    ("state", "State"),
    ("sheet_name", "Source"),
]


def _pipeline_compare(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Two merchants/identifiers side-by-side."""
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    pairs = task.get("names") or []
    not_found = []

    if len(values) >= 2:
        resolved = resolve_any(conn, values)
        left, right = values[0], values[1]
        a = resolved.get(left.upper().strip()) or {}
        b = resolved.get(right.upper().strip()) or {}
        if not a:
            not_found.append({"id": left, "kind": "any", "reason": "not in registry"})
        if not b:
            not_found.append({"id": right, "kind": "any", "reason": "not in registry"})
        left_label, right_label = left, right
    elif len(pairs) == 2:
        a_rows = _resolve_name_rows(pairs[0])
        b_rows = _resolve_name_rows(pairs[1])
        a = dict(a_rows[0]) if a_rows else {}
        b = dict(b_rows[0]) if b_rows else {}
        if not a_rows:
            not_found.append({"id": pairs[0], "kind": "name", "reason": "name not in registry"})
        if not b_rows:
            not_found.append({"id": pairs[1], "kind": "name", "reason": "name not in registry"})
        left_label, right_label = pairs[0], pairs[1]
    else:
        return {
            "intent": "compare",
            "pipeline": ["compare_merchants"],
            "summary": "Need two merchants or identifiers to compare - try "
                       "'compare LAGOON WATERS vs ARTEE INDUSTRIES'.",
            "columns": ["Field", "Entity A", "Entity B"],
            "rows": [], "not_found": [],
        }

    rows = [{"field": label, "entity_a": str(a.get(col) or ""),
             "entity_b": str(b.get(col) or "")}
            for col, label in _COMPARE_FIELDS]
    a_handles = {str(a[c]).upper().strip() for c in RESOLVE_COLS
                 if str(a.get(c) or "").strip()}
    b_handles = {str(b[c]).upper().strip() for c in RESOLVE_COLS
                 if str(b.get(c) or "").strip()}
    shared = sorted(a_handles & b_handles)
    verdict = (f"SHARES {len(shared)} identifier(s) - likely the same merchant "
               f"({', '.join(shared[:3])})." if shared else
               "No shared identifiers - distinct records.")
    return {
        "intent": "compare",
        "pipeline": ["compare_merchants"],
        "summary": f"Comparing {left_label} vs {right_label}. {verdict}",
        "columns": ["Field", "Entity A", "Entity B"],
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_coverage(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Coverage intent: segment rows where requested fields are MISSING.

    Delegates to the segment pipeline - _append_filters already applies the
    missing[] filters extracted by extract_params ('which NNPC stations have
    no email' -> rows matching NNPC with an empty email).
    """
    return _pipeline_segment(conn, task)


# Field -> (DB column, human label) for the ranking (top) intent.
_TOP_COLUMN = {
    "state": ("state", "State"),
    "bank": ("bank", "Bank"),
    "source": ("sheet_name", "Source file"),
    "sheet": ("sheet_name", "Source file"),
    "file": ("sheet_name", "Source file"),
    "merchant": ("merchant_name", "Merchant name"),
    "tid": ("tid", "TID"),
    "mxcode": ("mxcode", "MX code"),
    "contact": ("contact_name", "Contact name"),
    "email": ("email", "Email"),
    "phone": ("phone", "Phone"),
    "account": ("account_number", "Account number"),
    "onboarded": ("onboarded_date", "Onboarded date"),
}


def _pipeline_top(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Ranking: group rows by a field and return the top N by count.

    'top 10 banks in the NNPC file' -> 10 most common bank values among NNPC
    rows; 'how many merchants per state' -> per-state counts.
    """
    fields = task.get("segment_fields") or []
    col, label = None, None
    # Prefer the most meaningful groupable column when several matched
    # ('merchants per state' -> state, 'top 10 banks' -> bank).
    for f in fields:
        if f in _TOP_COLUMN and _TOP_COLUMN[f][0] != "merchant_name":
            col, label = _TOP_COLUMN[f]
            break
    if col is None:
        for f in fields:
            if f in _TOP_COLUMN:
                col, label = _TOP_COLUMN[f]
                break
    if col is None:
        return {
            "intent": "top",
            "pipeline": ["rank_top"],
            "summary": "No groupable field found - try 'top 10 banks in the NNPC file'.",
            "columns": [], "rows": [], "not_found": [],
        }
    seg = (task.get("segment") or "").strip()
    conds, params = "", []
    if seg:
        conds, params = _segment_where(seg)
        conds, params = _append_filters(conds, params, task.get("params") or {})
    where = f"WHERE {conds}" if conds else ""
    limit = min((task.get("params") or {}).get("limit") or 10, 100)
    rows = _fetch(
        conn,
        f"SELECT COALESCE(NULLIF(TRIM({col}),''), '(blank)') AS value, "
        f"COUNT(*) AS n FROM merchants {where} "
        f"GROUP BY value ORDER BY n DESC, value LIMIT {limit}",
        params)
    out = [{"value": r.get("value") or "", "count": r.get("n") or 0} for r in rows]
    summary = (f"Top {limit} by {label.lower()}" +
               (f" in '{seg}'" if seg else " in registry") +
               f": {len(out)} group(s).")
    return {
        "intent": "top",
        "pipeline": ["rank_top"],
        "summary": summary,
        "columns": [label, "Count"],
        "rows": out,
        "not_found": [] if out else [{"id": seg or "registry", "kind": "top",
                                       "reason": "no rows to rank"}],
    }


def _pipeline_verify(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Found/not-found check: is X in the registry?"""
    idents = task["identifiers"]
    values = [v for k in ID_KINDS for v in idents.get(k, [])]
    resolved = resolve_any(conn, values)
    rows, not_found = [], []
    for v in values:
        r = resolved.get(v.upper().strip())
        found = r is not None
        if not found:
            not_found.append({"id": v, "kind": "any", "reason": "not in registry"})
        rows.append({
            "identifier": v,
            "found": "Yes" if found else "No",
            "merchant": (r or {}).get("merchant_name") or "",
            "tid": (r or {}).get("tid") or "",
            "mxcode": (r or {}).get("mxcode") or "",
            "sheet": (r or {}).get("sheet_name") or "",
            "status": "found" if found else "not_found",
        })
    for n in task.get("names") or []:
        name_rows = _resolve_name_rows(n)
        found = bool(name_rows)
        if not found:
            not_found.append({"id": n, "kind": "name", "reason": "name not in registry"})
        rec = name_rows[0] if name_rows else {}
        rows.append({
            "identifier": n,
            "found": "Yes" if found else "No",
            "merchant": rec.get("merchant_name") or "",
            "tid": rec.get("tid") or "",
            "mxcode": rec.get("mxcode") or "",
            "sheet": rec.get("sheet_name") or "",
            "status": "found" if found else "not_found",
        })
    found_n = sum(1 for r in rows if r["found"] == "Yes")
    return {
        "intent": "verify",
        "pipeline": ["verify"],
        "summary": f"{found_n} of {len(rows)} input(s) found in the registry.",
        "columns": ["Identifier", "Found", "Merchant", "TID", "MX Code",
                    "Source", "Status"],
        "rows": rows,
        "not_found": not_found,
    }


def _pipeline_resolve(conn, task: Dict[str, Any]) -> Dict[str, Any]:
    """Any identifiers -> best registry rows (generic resolution)."""
    return _pipeline_field(conn, task, "mxcode", "MX Code", task.get("intent", "resolve"))


_PIPELINES = {
    "static_account": _pipeline_static_account,
    "tid": _pipeline_tid,
    "email": _pipeline_email,
    "phone": _pipeline_phone,
    "address": _pipeline_address,
    "bank": _pipeline_bank,
    "account_name": _pipeline_account_name,
    "account_number": _pipeline_account_number,
    "payable": _pipeline_payable,
    "alias": _pipeline_alias,
    "contact": _pipeline_contact,
    "onboarded": _pipeline_onboarded,
    "state": _pipeline_state,
    "source": _pipeline_source,
    "beneficiary": _pipeline_beneficiary,
    "related": _pipeline_related,
    "formerly": _pipeline_formerly,
    "compare": _pipeline_compare,
    "coverage": _pipeline_coverage,
    "top": _pipeline_top,
    "verify": _pipeline_verify,
    "mxcode": _pipeline_mxcode,
    "profile": _pipeline_profile,
    "change_details": _pipeline_change_details,
    "segment": _pipeline_segment,
    "count": _pipeline_count,
    "duplicates": _pipeline_duplicates,
    "summary": _pipeline_summary,
}


# Produced registry fields -> the identifier kinds a later workflow step can
# consume (resolve_any / the pipelines read these). 'name' values feed the
# task's names list. Only fields that are genuinely identifier-like are
# threaded — informational columns (address, bank, ...) never are.
_PRODUCED_FIELD_KINDS = {
    "mxcode": "mxcode",
    "tid": "tid",
    "phone": "phone",
    "email": "email",
    "static_acc_no": "static",
    "account_number": "account",
    "payable_code": "payable",
    "alias": "alias",
    "beneficiary": "name",
    "merchant": "name",
}


def extract_produced_values(result: Dict[str, Any],
                            produced_fields: List[str]) -> Dict[str, Any]:
    """Pull the identifier-like values a pipeline's result rows PRODUCED.

    Feeds the workflow executor: step P's rows produce values that step S
    (whose `requires` names P) can consume as identifiers or names.
    Returns {"identifiers": {kind: [values]}, "names": [names]} with
    per-(kind, value) dedup.
    """
    out_ids: Dict[str, List[str]] = {}
    names: List[str] = []
    seen: set = set()
    for field in produced_fields or []:
        kind = _PRODUCED_FIELD_KINDS.get(field)
        if kind is None:
            continue
        for r in result.get("rows", []):
            v = r.get(field)
            if not v:
                continue
            key = (kind, str(v).strip().upper())
            if key in seen:
                continue
            seen.add(key)
            if kind == "name":
                names.append(str(v).strip())
            else:
                out_ids.setdefault(kind, []).append(str(v).strip())
    return {"identifiers": out_ids, "names": names}


def _merge_tables(tables: List[Dict[str, Any]], intents: List[str]) -> Dict[str, Any]:
    """Merge multiple intent tables into one (feature #4, compound intents).

    Rows are joined on the 'identifier' field; non-empty fields are unioned
    so 'mxcode AND email' yields one row per identifier with both columns.
    """
    if len(tables) == 1:
        return tables[0]
    cols: List[str] = []
    for t in tables:
        for c in t.get("columns", []):
            if c not in cols:
                cols.append(c)
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for t in tables:
        for r in t.get("rows", []):
            key = r.get("identifier") or r.get("tid") or ""
            if not key:
                continue
            if key not in merged:
                merged[key] = {}
                order.append(key)
            for k, v in r.items():
                if v and not merged[key].get(k):
                    merged[key][k] = v
    rows = [merged[k] for k in order]
    not_found = [nf for t in tables for nf in t.get("not_found", [])]
    return {
        "intent": "+".join(intents),
        "pipeline": [s for t in tables for s in t.get("pipeline", [])],
        "summary": "Merged " + ", ".join(intents) + f" for {len(rows)} identifier(s).",
        "columns": cols,
        "rows": rows,
        "not_found": not_found,
    }
