"""Admin / batch router — report, learn, quickmatch, batch, quality,
reconcile, brief, self-improve status, and the task Excel export.

Handlers moved verbatim from api.py during the router split; paths and
response shapes are unchanged.
"""

import json
import re
import sqlite3
import time
from io import BytesIO

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api_shared import (
    _audit,
    _key_merchants_for,
    _quick_match_rows,
    _style_workbook,
    config,
    get_profiler,
    get_searcher,
    BatchRequest,
    LearnRequest,
    ProfileRequest,
    QuickMatchRequest,
    TaskRequest,
)

router = APIRouter()


# Header -> row key mapping for task export (mirrors the frontend's
# TASK_COLUMN_KEYS) so every pipeline — static_account, email/phone/mx,
# profile, change_details, segment, count, duplicates, summary — exports
# its full column set instead of the old hard-coded subset.
_TASK_EXPORT_KEY_BY_HEADER = {
    "TID": "tid", "Merchant": "merchant", "Merchant Name": "merchant",
    "MX Code": "mxcode", "Static Account Number": "static_acc_no",
    "Beneficiary": "beneficiary", "Payable Code": "payable_code",
    "Payable": "payable", "Alias": "alias", "Bank": "bank",
    "Status": "status", "Identifier": "identifier", "Phone": "phone",
    "Email": "email", "Slip Header": "slip_header", "Source": "sheet",
    "Sheet": "sheet", "Account Name": "account_name",
    "Account Number": "account_number", "Contact": "contact",
    "Address": "address", "State": "state", "Onboarded": "onboarded",
    "Row": "row", "Metric": "metric", "Count": "count", "Value": "value",
    "Rows": "rows", "Sources": "sources", "Current Account": "current_acc",
    "Current Bank": "current_bank", "Changed": "change_detected",
    # change-details old/new pairs are stored under the exact header text
    "Old Bank Acc No": "Old Bank Acc No", "New Bank Acc No": "New Bank Acc No",
    "Old Bank Code": "Old Bank Code", "New Bank Code": "New Bank Code",
    "Old Address": "Old Address", "New Address": "New Address",
    "Old Account Name": "Old Account Name", "New Account Name": "New Account Name",
}

# Columns whose row key varies by pipeline. Resolution order mirrors the
# frontend: prefer the row's own key, fall back through the alternates.
#   State  — segment rows carry 'state'; profile rows store state under 'bank'
#   Source — segment rows use 'source'; every other pipeline uses 'sheet'
_TASK_EXPORT_ALTERNATES = {
    "State": ["state", "bank"],
    "Source": ["source", "sheet"],
}


def _task_export_snake(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower())


def _task_export_frame(result):
    """Result rows + columns -> export-ready list of dicts (human headers).

    Resolves each column's row key the same way the frontend does (mapped
    key, exact label for change-pair rows, snake_case, per-column alternates),
    then appends any extra row fields the pipeline produced so nothing is lost.
    """
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not rows:
        return []
    used = set()
    for c in columns:
        key = _TASK_EXPORT_KEY_BY_HEADER.get(c) or _task_export_snake(c)
        used.add(key)
        used.add(c)
        used.update(_TASK_EXPORT_ALTERNATES.get(c, []))
    extra_keys = list(dict.fromkeys(k for r in rows for k in r if k not in used))
    out = []
    for r in rows:
        row = {}
        for c in columns:
            key = _TASK_EXPORT_KEY_BY_HEADER.get(c) or _task_export_snake(c)
            val = r.get(key)
            if val in (None, ""):
                val = r.get(c)  # exact-label keys (change old/new pairs)
            if val in (None, ""):
                val = r.get(_task_export_snake(c))
            if val in (None, ""):
                for alt in _TASK_EXPORT_ALTERNATES.get(c, []):
                    v = r.get(alt)
                    if v not in (None, ""):
                        val = v
                        break
            row[c] = "" if val is None else val
        for k in extra_keys:
            if k not in row:
                row[k] = "" if r.get(k) is None else r.get(k)
        out.append(row)
    return out


def _run_batch(merchants: list[str]) -> list[dict]:
    """Run one search per merchant, returning the best match per input."""
    searcher = get_searcher()
    rows = []
    for m in merchants:
        res = searcher.search(m, limit=1, min_score=0)
        best = res[0] if res else None
        rec = best.record if best else {}
        rows.append({
            "input": m,
            "best_match": rec.get("merchant_name", ""),
            "score": round(best.overall_score / 10, 1) if best else 0,
            "match_type": best.match_type if best else "Not Found",
            "email": rec.get("email", ""),
            "phone": rec.get("phone", ""),
            "tid": rec.get("tid", ""),
            "mxcode": rec.get("mxcode", ""),
            "sheet": rec.get("sheet_name", ""),
            # Key-merchant roots for the matched name so Batch rows carry the
            # same family badge as the Search page (clickable -> profile).
            "key_merchants": _key_merchants_for(rec.get("merchant_name", "")),
        })
    return rows


@router.post("/report")
def report(req: BatchRequest):
    """Phase 9 Merchant Intelligence Report — full multi-sheet preview."""
    from report import build_report
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    if not merchants:
        raise HTTPException(status_code=400, detail="merchants is empty")
    t0 = time.perf_counter()
    rep = build_report(merchants, top_n=3)
    elapsed = time.perf_counter() - t0

    def recs(key):
        df = rep[key]
        return df.to_dict(orient="records") if df is not None and not df.empty else []

    keys = ["exact", "high", "possible", "emails", "phones",
            "contacts", "addresses", "duplicates", "not_found"]
    return {
        "count": len(merchants),
        "elapsed_s": round(elapsed, 2),
        "summary": recs("summary"),
        "sheet_counts": {k: len(recs(k)) for k in keys},
        "sheets": {k: recs(k) for k in keys},
    }


@router.post("/report/export")
def report_export(req: BatchRequest):
    """Build the Phase 9 report and download it as a multi-sheet workbook."""
    from report import build_report, SHEET_ORDER, SHEET_NAMES
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    if not merchants:
        raise HTTPException(status_code=400, detail="merchants is empty")
    _audit("export", json.dumps({"kind": "report", "count": len(merchants)}))
    rep = build_report(merchants, top_n=3)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for key in SHEET_ORDER:
            df = rep[key]
            if df is not None and not df.empty:
                df.to_excel(writer, sheet_name=SHEET_NAMES[key], index=False)
            else:
                pd.DataFrame({"Note": ["No records"]}).to_excel(
                    writer, sheet_name=SHEET_NAMES[key], index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Merchant_Intelligence_Report.xlsx"'},
    )


@router.post("/learn")
def learn(req: LearnRequest):
    """Teach the alias engine a new query -> merchant mapping (Phase 10)."""
    query = req.query.strip()
    merchant = req.merchant_name.strip()
    if not query or not merchant:
        raise HTTPException(status_code=400, detail="query and merchant_name are required")
    # Guard: only persist mappings to merchants that exist in the registry.
    conn = None
    try:
        conn = sqlite3.connect(str(config.active_db()))
        exists = conn.execute(
            "SELECT COUNT(*) FROM merchants WHERE UPPER(merchant_name) = ?",
            (merchant.upper(),),
        ).fetchone()[0] > 0
    except Exception:
        exists = True  # fail-open if DB check itself fails
    finally:
        if conn:
            conn.close()
    if not exists:
        raise HTTPException(status_code=400,
                            detail=f"merchant_name not found in registry: {merchant}")
    engine = get_searcher().matcher.alias_engine
    learned = engine.learn(query, merchant)
    return {"learned": learned, "query": query, "merchant_name": merchant}


@router.post("/quickmatch")
def quickmatch(req: QuickMatchRequest):
    """Resolve a batch of identifiers (phones, MX codes, TIDs, emails, account
    numbers) against the registry. Unlike /api/batch (name fuzzy matching),
    this is precision-first: a record only counts as matched when the
    identifier itself was found."""
    identifiers = [i.strip() for i in req.identifiers if i.strip()][:2000]
    if not identifiers:
        raise HTTPException(status_code=400, detail="identifiers is empty")
    t0 = time.perf_counter()
    rows = _quick_match_rows(identifiers)
    elapsed = time.perf_counter() - t0

    matched = sum(1 for r in rows if r["matched"])
    emails = sum(1 for r in rows if r["email"])
    return {
        "count": len(rows),
        "elapsed_s": round(elapsed, 2),
        "matched": matched,
        "missing": len(rows) - matched,
        "emails": emails,
        "pct": round(matched / len(rows) * 100) if rows else 0,
        "rows": rows,
    }


@router.post("/quickmatch/export")
def quickmatch_export(req: QuickMatchRequest):
    """Resolve identifiers and export the results as an Excel workbook."""
    identifiers = [i.strip() for i in req.identifiers if i.strip()][:2000]
    if not identifiers:
        raise HTTPException(status_code=400, detail="identifiers is empty")
    _audit("export", json.dumps({"kind": "quickmatch",
                                 "count": len(identifiers)}))
    rows = _quick_match_rows(identifiers)
    df = pd.DataFrame(rows)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Quick Match", index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="quick_match_results.xlsx"'},
    )


@router.post("/task/export")
def task_export(req: TaskRequest):
    """Interpret a pasted request, execute it, and export the result table as
    an Excel workbook (used by the Export button on task results)."""
    from merchant_intelligence import tasks
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        detected = tasks.detect_task(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not detected:
        raise HTTPException(status_code=400, detail="not a task - nothing to export")
    _audit("export", json.dumps({"kind": "task", "text": text[:300],
                                 "intent": detected.get("intent")}))
    result = tasks.execute_task(detected)
    rows = result.get("rows", [])
    columns = result.get("columns", [])
    if not columns or not rows:
        raise HTTPException(status_code=400, detail="task produced no rows to export")
    df = pd.DataFrame(_task_export_frame(result))
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Task Results", index=False)
        pd.DataFrame({"Note": [result.get("summary", "")]}).to_excel(
            writer, sheet_name="Summary", index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="task_results.xlsx"'},
    )


@router.post("/batch")
def batch(req: BatchRequest):
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    if not merchants:
        raise HTTPException(status_code=400, detail="merchants is empty")
    _audit("batch", json.dumps({"count": len(merchants)}))
    t0 = time.perf_counter()
    rows = _run_batch(merchants)
    elapsed = time.perf_counter() - t0

    found = sum(1 for r in rows if r["score"] >= 5.0)
    emails = sum(1 for r in rows if r["email"])
    return {
        "count": len(rows),
        "elapsed_s": round(elapsed, 2),
        "found": found,
        "missing": len(rows) - found,
        "emails": emails,
        "pct": round(found / len(rows) * 100) if rows else 0,
        "rows": rows,
    }


@router.post("/batch/export")
def batch_export(req: BatchRequest):
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    rows = _run_batch(merchants)
    _audit("export", json.dumps({"kind": "batch", "count": len(rows)}))
    df = pd.DataFrame(rows)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Batch Search", index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="batch_search_results.xlsx"'},
    )


@router.get("/quality")
def quality():
    """Data quality scan of the registry (reuses data_quality.run_quality)."""
    from data_quality import run_quality
    q = run_quality()
    total = q["total"] or 0
    return {
        "total": total,
        "sheets": q.get("sheets", {}),
        "missing": q.get("missing", {}),
        "code_names": q.get("code_names", 0),
        "orphans": q.get("orphans", 0),
        "duplicate_tids": q["duplicate_tids"].to_dict(orient="records"),
        "mx_multiname": q["mx_multiname"].to_dict(orient="records"),
    }


@router.post("/quality/export")
def quality_export():
    """Export the data quality report as an Excel workbook."""
    from data_quality import run_quality
    _audit("export", json.dumps({"kind": "quality"}))
    q = run_quality()
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({
            "Metric": ["Total records", "Code names", "Orphans"]
            + [f"Missing {f}" for f in q["missing"]],
            "Value": [q["total"], q["code_names"], q["orphans"]]
            + [q["missing"][f] for f in q["missing"]],
        }).to_excel(writer, sheet_name="Summary", index=False)
        q["duplicate_tids"].to_excel(writer, sheet_name="Duplicate TIDs", index=False)
        q["mx_multiname"].to_excel(writer, sheet_name="MX Multi-Name", index=False)
        pd.DataFrame(
            [{"Sheet": s, "Records": n} for s, n in q.get("sheets", {}).items()]
        ).to_excel(writer, sheet_name="Sheets", index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="data_quality_report.xlsx"'},
    )


@router.post("/reconcile")
def reconcile_endpoint(req: BatchRequest):
    """Reconcile a merchant list into verified matches + recovered assets."""
    from reconcile import reconcile as run_reconcile
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    if not merchants:
        raise HTTPException(status_code=400, detail="merchants is empty")
    _audit("reconcile", json.dumps({"count": len(merchants)}))
    t0 = time.perf_counter()
    report = run_reconcile(merchants, top_n=3)
    elapsed = time.perf_counter() - t0

    def rows(name):
        df = report[name]
        return df.to_dict(orient="records") if df is not None and not df.empty else []

    matches = rows("matches")
    not_found = rows("not_found")
    emails = rows("emails")
    contacts = rows("contacts")
    return {
        "count": len(merchants),
        "elapsed_s": round(elapsed, 2),
        "found": len(matches),
        "missing": len(not_found),
        "emails": len(emails),
        "contacts": len(contacts),
        "pct": round(len(matches) / len(merchants) * 100) if merchants else 0,
        "matches": matches,
        "not_found": not_found,
        "emails_rows": emails,
        "contacts_rows": contacts,
        "summary": rows("summary"),
        "all_results": rows("all_results"),
    }


@router.post("/brief")
def brief(req: ProfileRequest):
    """LLM investigation brief for a merchant fragment (feature #6).

    Builds the 360° profile for the query (any fragment — name, email, phone,
    MX code, TID, account number…) then produces a natural-language
    investigation dossier. Uses an LLM when LLM_API_KEY is configured
    (any OpenAI-compatible endpoint via LLM_BASE_URL/LLM_MODEL); otherwise
    falls back to a deterministic offline template brief.
    """
    from merchant_intelligence.brief import build_brief, llm_available
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    _audit("brief", json.dumps({"query": query}))
    profile = get_profiler().build(query, max_members=req.max_members)
    out = build_brief(profile)
    out["query"] = query
    out["llm_configured"] = llm_available()
    return out


@router.get("/selfimprove")
def selfimprove_status():
    """Last alias-free harness run + current baseline (feature #10).

    Returns the stored self-improve report (data/self_improve_report.json) and
    baseline (data/alias_free_baseline.json) so the frontend can show engine
    health. Empty dicts when the harness has not run yet.
    """
    out = {"report": None, "baseline": None}
    for key, name in (("report", "self_improve_report.json"),
                      ("baseline", "alias_free_baseline.json")):
        try:
            path = config.DATA_DIR / name
            if path.exists():
                out[key] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            out[key] = None
    return out


@router.post("/reconcile/export")
def reconcile_export(req: BatchRequest):
    """Run reconciliation and export the full report as Excel."""
    from reconcile import reconcile as run_reconcile
    merchants = [m.strip() for m in req.merchants if m.strip()][:1000]
    if not merchants:
        raise HTTPException(status_code=400, detail="merchants is empty")
    report = run_reconcile(merchants, top_n=3)
    buffer = BytesIO()
    sheet_names = {
        "summary": "Summary", "matches": "Matches", "not_found": "Not Found",
        "all_results": "All Results", "emails": "Emails", "contacts": "Contacts",
    }
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for key, name in sheet_names.items():
            df = report[key]
            if df is not None and not df.empty:
                df.to_excel(writer, sheet_name=name, index=False)
            else:
                pd.DataFrame({"Note": ["No records"]}).to_excel(writer, sheet_name=name, index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Merchant_Reconciliation_Report.xlsx"'},
    )


# ── Drift monitoring (roadmap #5 — quality scans) ───────────────────────

@router.get("/drift-scan")
def drift_scan_endpoint():
    """Run all drift quality scans (routing, recall, freshness)."""
    from merchant_intelligence.drift import scan_all
    _audit("drift_scan")
    return scan_all()


@router.get("/drift-history")
def drift_history_endpoint(n: int = 20):
    """Read recent drift scan history."""
    from merchant_intelligence.drift import recent_history
    return {"history": recent_history(n)}


# ── Telemetry (roadmap #3 — observability) ──────────────────────────────

@router.get("/telemetry")
def telemetry_endpoint(n: int = 50, trace_type: str = None):
    """Read recent telemetry records."""
    from merchant_intelligence.telemetry import recent_telemetry
    _audit("telemetry_view")
    return {"records": recent_telemetry(n, trace_type)}


@router.get("/telemetry/stats")
def telemetry_stats_endpoint():
    """Aggregate telemetry stats for the admin dashboard."""
    from merchant_intelligence.telemetry import telemetry_stats
    return telemetry_stats()


# ── Governed learned assets (roadmap #5) ────────────────────────────────

@router.get("/assets/pending")
def assets_pending_endpoint():
    """List all assets awaiting review."""
    from merchant_intelligence.governed import get_pending_assets
    _audit("assets_pending")
    return {"pending": get_pending_assets()}


@router.get("/assets/history")
def assets_history_endpoint(n: int = 50, asset_type: str = None):
    """Read recent asset events."""
    from merchant_intelligence.governed import get_asset_history
    return {"events": get_asset_history(asset_type, n)}


@router.post("/assets/{asset_type}/{asset_id}/approve")
def assets_approve_endpoint(asset_type: str, asset_id: str, version: int):
    """Approve a proposed asset version."""
    from merchant_intelligence.governed import approve_asset
    _audit("asset_approve", {"type": asset_type, "id": asset_id, "version": version})
    return approve_asset(asset_type, asset_id, version)


@router.post("/assets/{asset_type}/{asset_id}/reject")
def assets_reject_endpoint(asset_type: str, asset_id: str, version: int,
                           reason: str = ""):
    """Reject a proposed asset version."""
    from merchant_intelligence.governed import reject_asset
    _audit("asset_reject", {"type": asset_type, "id": asset_id, "version": version})
    return reject_asset(asset_type, asset_id, version, reason=reason)


@router.post("/assets/{asset_type}/{asset_id}/apply")
def assets_apply_endpoint(asset_type: str, asset_id: str, version: int):
    """Apply an approved asset."""
    from merchant_intelligence.governed import apply_asset
    _audit("asset_apply", {"type": asset_type, "id": asset_id, "version": version})
    return apply_asset(asset_type, asset_id, version)


# ── Schema migration endpoint ───────────────────────────────────────────

@router.post("/schema/migrate")
def schema_migrate_endpoint():
    """Run the normalized schema migration."""
    from merchant_intelligence.schema import migrate, populate_identifiers, build_entity_clusters
    _audit("schema_migrate")
    result = migrate()
    if result["ok"]:
        result["identifiers"] = populate_identifiers()
        result["clusters"] = build_entity_clusters()
    return result


# ── Incremental ingestion endpoint ──────────────────────────────────────

@router.post("/ingestion/scan")
def ingestion_scan_endpoint():
    """Run an incremental ingestion scan."""
    from merchant_intelligence.ingestion import run_incremental_scan
    _audit("ingestion_scan")
    return run_incremental_scan()
