"""Search router — search, entity graph, exports, autocomplete, suggestions.

Handlers moved verbatim from api.py during the router split; paths and
response shapes are unchanged.
"""

import json
import re
import sqlite3
import time
from io import BytesIO
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api_shared import (
    _audit,
    _key_merchants_for,
    _search_with_multi,
    _style_workbook,
    config,
    damerau_levenshtein_similarity,
    get_resolver,
    get_searcher,
    SearchRequest,
    EntityRequest,
)

router = APIRouter()


class AliasAction(BaseModel):
    alias: str
    canonical: str


@router.post("/search")
def search(req: SearchRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    _audit("search", json.dumps({"query": query,
                                 "offset": req.offset, "limit": req.limit}))
    t0 = time.perf_counter()
    fetch = min(req.offset + req.limit, 200)
    results = _search_with_multi(query, fetch, req.min_score)
    total = len(results)
    page = results[req.offset:req.offset + req.limit]
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Remember this search as follow-up context — a subsequent "get the tids
    # for the above merchant" resolves against it (DB-grounded: the top
    # result's own identifiers + canonical name, never the raw query text).
    try:
        if results:
            top = results[0].to_dict()
            ids = {}
            for k in ("tid", "mxcode", "phone", "email"):
                v = top.get(k)
                if v:
                    ids.setdefault(k, []).append(str(v))
            name = top.get("merchant_name")
            from merchant_intelligence import tasks as _tasks
            _tasks.remember_entities(identifiers=ids,
                                     names=[name] if name else None)
    except Exception:
        pass
    # Feed the search into the self-improvement loop (rephrase detection +
    # outcome stats). A search with zero results is a candidate "failed
    # phrasing" — if the same merchant is re-asked shortly after, the search
    # is tagged rephrased and the follow-up's intent becomes the correction.
    try:
        from merchant_intelligence import feedback as _fb
        _fb.log_request(kind="search", text=query, intent="", rows=total,
                        entity_sig=[query.upper().strip()])
    except Exception:
        pass
    return {
        "query": query,
        "count": len(page),
        "total": total,
        "offset": req.offset,
        "elapsed_ms": round(elapsed_ms, 1),
        "results": [r.to_dict() for r in page],
    }


@router.post("/entity")
def entity(req: EntityRequest):
    """Entity graph: merchant family + BFS relationship graph.

    Returns the family (records linked to the seed by shared identifiers),
    the raw graph (nodes + edges for visualisation) and alias candidates
    that the frontend can offer to teach the engine.
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    t0 = time.perf_counter()
    resolver = get_resolver()
    family = resolver.family_of(query)
    graph = resolver.graph(query, depth=req.depth, max_nodes=req.max_nodes)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Annotate each family member with a name-similarity % vs the seed so the
    # frontend can rank node cards (mirrors the Streamlit tab behaviour).
    members = []
    for m in family.get("members", []):
        mname = str(m.get("merchant_name", ""))
        sim = token_sort_ratio(query, mname)
        m = dict(m)
        m["match_pct"] = max(int(sim * 100), 30) if mname else 0
        members.append(m)
    family["members"] = members

    return {
        "seed": query,
        "elapsed_ms": round(elapsed_ms, 1),
        "family": family,
        "graph": graph,
    }


@router.post("/search/export")
def search_export(req: SearchRequest):
    """Export the current search view as an Excel workbook."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    _audit("export", json.dumps({"kind": "search", "query": query}))
    fetch = min(req.offset + req.limit, 200)
    results = _search_with_multi(query, fetch, req.min_score)
    page = results[req.offset:req.offset + req.limit]
    rows = []
    for r in page:
        rec = r.record
        rows.append({
            "Score": round(r.overall_score / 10, 1),
            "Match Type": r.match_type,
            "Merchant Name": rec.get("merchant_name", ""),
            "TID": rec.get("tid", ""),
            "MX Code": rec.get("mxcode", ""),
            "Email": rec.get("email", ""),
            "Phone": rec.get("phone", ""),
            "Contact Name": rec.get("contact_name", ""),
            "Account Name": rec.get("account_name", ""),
            "Onboarded": rec.get("onboarded_date", ""),
            "Sheet": rec.get("sheet_name", ""),
            "Matched By": r.identifier_hit or "",
            # Build-time data-quality score (0-100) so weak records are
            # visible in the exported workbook too.
            "Quality": rec.get("quality_score", 100),
        })
    df = pd.DataFrame(rows)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Search Results", index=False)
        _style_workbook(writer.book)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="search_results.xlsx"'},
    )


@router.get("/idclass/debug")
def idclass_debug(values: str = "", text: str = "", limit: int = 50):
    """Debug the DB-rooted identifier classifier (idclass.py).

    GET /api/idclass/debug?values=MX184380,2103O338,5180857349
    GET /api/idclass/debug?text=MX184380 2103O338 FELIX

    For each pasted token, shows WHY it classified the way it did:
      source         db_membership | shape_rule | rejected | unknown
      in_db_columns  the actual registry columns storing that value
      shape_rule     the shape pattern that matched (non-DB values)
      reason         human-readable explanation
    Plus diagnostics about the index itself (DB path, distinct tokens,
    per-kind counts) so you can verify which DB the engine is reading.
    """
    from merchant_intelligence.idclass import get_classifier

    tokens = []
    if values:
        tokens += [v.strip() for v in values.split(",") if v.strip()]
    if text:
        tokens += [t for t in re.split(r"[\s,;]+", text) if t.strip()]
    # Dedup preserving order, cap the result set.
    seen, uniq = set(), []
    for t in tokens:
        key = t.upper()
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    uniq = uniq[:max(1, min(limit, 200))]

    classifier = get_classifier()
    stats = classifier.index_stats()
    return {
        "index": stats,
        "count": len(uniq),
        "results": [classifier.inspect(t) for t in uniq],
    }


def _prefix_edit_similarity(typed: str, candidate: str) -> float:
    """0..1 similarity of the TYPED prefix against the START of a candidate.

    Unlike token_set_ratio (which splits on spaces and can't compare a
    single unbroken token), this measures how many edits turn the typed
    prefix into a PREFIX of the candidate bucket key:
      'MEDPLUZ' -> 'MEDPLUS'  1 edit (substitution)  -> high
      'KONGOPAY' -> 'KONGAPAY' 1 edit                 -> high
      'lagoon watr' -> 'LAGOON WATERS' 1 edit         -> high
      'ZZZZNOTREAL' -> any key  ~9 edits             -> ~0

    Transpositions count as ONE edit (Damerau-Levenshtein), so 'MEDPLUS' vs
    'MEDLPUS' also recovers. Returns 0 when the candidate doesn't start with
    a near-edit version of the typed prefix (no accidental mid-name hits).
    """
    typed = (typed or "").upper().strip()
    candidate = (candidate or "").upper().strip()
    if not typed or not candidate or len(typed) < 3:
        return 0.0
    # DELIBERATE TRADEOFF: candidates must share the typed FIRST character.
    # Real typos almost always hit mid-word (MEDPLUZ), and this gate prunes
    # ~96% of the 13k-key scan before any distance call — keeping the
    # keystroke path sub-millisecond. A first-letter typo ('edplus') does
    # NOT recover here; the full search still finds the merchant, and the
    # autocomplete dropdown just stays silent for that rare case.
    if candidate[0] != typed[0]:
        return 0.0
    limit = min(len(typed), len(candidate))
    sim = damerau_levenshtein_similarity(typed[:limit], candidate[:limit])
    if sim < 0.72:
        return 0.0
    return sim


def _fuzzy_prefix_suggestions(prefix: str, keys, limit: int) -> list:
    """Character-level fuzzy tier: recover bucket keys whose START is a
    typo'd version of the typed prefix (single-token typos the token-based
    tier cannot see). Returns candidates sorted best-first, best first."""
    scored = []
    for k in keys:
        sim = _prefix_edit_similarity(prefix, k)
        if sim > 0.0:
            scored.append((sim, k))
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    return [k for _s, k in scored[:limit]]


@router.get("/autocomplete")
def autocomplete(prefix: str = "", limit: int = 8):
    """Live typeahead suggestions from the normalized name_buckets table.

    GET /api/autocomplete?prefix=lagoon&limit=8

    Returns merchant bucket keys whose canonical form starts with the
    prefix ("lagoon wat" -> ["LAGOON WATERS", ...]). Backed by one indexed
    LIKE query on the bucket_key primary key, so it is fast enough to fire
    on every keystroke. Ensures the bucket table is built lazily on first
    use so existing databases work without a rebuild.

    When the exact-prefix tier comes up short, two fuzzy fallbacks recover
    near-misses (both are niceties — never break the page):
      1. token_set_ratio scan over the distinct-name index — recovers
         multi-word phrases with added/missing tokens ("lagoon water ent").
      2. Damerau-Levenshtein PREFIX scan — recovers single-token typos
         ("medpluz" -> MEDPLUS, "kongopay" -> KONGAPAY) that the token
         splitter cannot see.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return {"prefix": prefix, "suggestions": []}
    try:
        db = get_searcher().matcher.db
        db.ensure_buckets()
        suggestions = db.autocomplete(prefix, limit=max(1, min(limit, 15)))
        if len(suggestions) < limit:
            keys = db.bucket_keys()
            if keys:
                from rapidfuzz import fuzz as _rf_fuzz
                from rapidfuzz import process as _rf_process
                # 1) token_set_ratio (NOT WRatio — its partial component
                #    over-matches garbage like 'ZZZZNOTREAL' against short keys)
                fuzzy = [k for k, _s, _i in _rf_process.extract(
                    prefix, keys, scorer=_rf_fuzz.token_set_ratio,
                    limit=max(1, min(limit, 15)), score_cutoff=70)
                    if k not in suggestions]
                suggestions = (suggestions + fuzzy)[:max(1, min(limit, 15))]
        # 2) character-level prefix tier: single-token typos the token
        #    splitter cannot see ('medpluz' -> MEDPLUS). Always runs so a
        #    typo'd first keypress recovers even when the token tier found
        #    nothing.
        if len(suggestions) < limit:
            keys = db.bucket_keys()
            if keys:
                extra = [k for k in _fuzzy_prefix_suggestions(prefix, keys, limit)
                         if k not in suggestions]
                suggestions = (suggestions + extra)[:max(1, min(limit, 15))]
    except Exception:
        suggestions = []  # autocomplete is a nicety — never break the page
    return {"prefix": prefix, "suggestions": suggestions}


@router.post("/suggest")
def suggest(req: SearchRequest):
    """Did-you-mean suggestions when a query yields no confident hits.

    Generates variants of the query (dropped tokens, common typo fixes,
    generic-word removal) and returns the ones that DO find records.
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    searcher = get_searcher()
    base = searcher.search(query, limit=3, min_score=config.POSSIBLE_THRESHOLD)
    if base:
        return {"query": query, "suggestions": []}

    suggestions = []
    seen = set()

    def try_add(q):
        q = (q or "").strip()
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        res = searcher.search(q, limit=1, min_score=config.POSSIBLE_THRESHOLD)
        if res:
            suggestions.append({
                "query": q,
                "score": round(res[0].overall_score / 10, 1),
                "best_match": res[0].record.get("merchant_name", ""),
            })

    tokens = query.split()
    # 1. Drop each token in turn
    for i in range(len(tokens)):
        try_add(" ".join(tokens[:i] + tokens[i + 1:]))
    # 2. Common spelling fixes (canonicalize applies these at token level
    #    already; here we also retry the RAW query with each correction in
    #    case the fix changes which DB rows are retrievable).
    fixes = dict(config.TYPO_FIXES)
    fixes["BIDGBENGA"] = "BIDDEL"  # query-side name, not a typo of a word
    ql = query.lower()
    for bad, good in fixes.items():
        if bad.lower() in ql:
            try_add(ql.replace(bad.lower(), good.lower()))
    # 3. Strip generic words entirely
    sig = [t for t in tokens if t.upper() not in config.GENERIC_WORDS]
    if len(sig) != len(tokens) and sig:
        try_add(" ".join(sig))

    return {"query": query, "suggestions": suggestions[:6]}


@router.post("/similar")
def similar(req: SearchRequest):
    """Similar merchants: records linked to the query by shared identifiers
    (entity family) — different names that resolve to the same merchant."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    resolver = get_resolver()
    family = resolver.family_of(query)
    members = family.get("members", [])
    seen = set()
    out = []
    for m in members:
        name = m.get("merchant_name", "") or ""
        key = name.upper()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({
            "merchant_name": name,
            "sheet": m.get("sheet_name", ""),
            "email": m.get("email", ""),
            "phone": m.get("phone", ""),
            "mxcode": m.get("mxcode", ""),
            "tid": m.get("tid", ""),
            "link_reasons": m.get("link_reasons", []),
            # Key-merchant roots (same engine the search badge uses) so the
            # Similar/Related panel can show the same family chip + click.
            "key_merchants": _key_merchants_for(name),
        })
    return {"query": query, "count": len(out), "similar": out[:req.limit]}


@router.get("/duplicates")
def duplicates(limit: int = 100):
    """Duplicate merchant clusters: the same merchant name appearing on
    multiple DB rows (across sheets/files), grouped with locations."""
    conn = sqlite3.connect(str(config.active_db()))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    groups = c.execute(
        "SELECT UPPER(merchant_name) name_key, COUNT(*) n "
        "FROM merchants WHERE merchant_name != '' "
        "GROUP BY name_key HAVING n > 1 ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    clusters = []
    for g in groups:
        rows = c.execute(
            "SELECT id, merchant_name, sheet_name, row_number, tid, mxcode, email "
            "FROM merchants WHERE UPPER(merchant_name) = ?",
            (g["name_key"],),
        ).fetchall()
        clusters.append({
            "merchant_name": rows[0]["merchant_name"],
            "occurrences": len(rows),
            "sheets": sorted({r["sheet_name"] for r in rows}),
            "records": [{
                "id": r["id"], "sheet": r["sheet_name"],
                "row": r["row_number"], "tid": r["tid"],
                "mxcode": r["mxcode"], "email": r["email"],
            } for r in rows],
        })
    conn.close()
    return {"count": len(clusters), "clusters": clusters}


@router.get("/aliases")
def aliases():
    """Alias review queue: manual + learned aliases with approval status."""
    engine = get_searcher().matcher.alias_engine
    learned = engine.review_items()
    manual = engine.manual_items()
    return {
        "learned": learned,
        "manual": manual,
        "counts": {
            "learned": len(learned),
            "manual": len(manual),
            "pending": sum(1 for i in learned if i["status"] == "pending"),
            "approved": sum(1 for i in learned if i["status"] == "approved"),
        },
    }


@router.post("/aliases/approve")
def alias_approve(req: AliasAction):
    """Approve a learned alias in the review queue."""
    engine = get_searcher().matcher.alias_engine
    return {"ok": engine.approve(req.alias, req.canonical)}


@router.post("/aliases/reject")
def alias_reject(req: AliasAction):
    """Reject (forget) a learned alias in the review queue."""
    engine = get_searcher().matcher.alias_engine
    return {"ok": engine.forget(req.alias, req.canonical)}
