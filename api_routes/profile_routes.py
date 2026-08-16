"""Profile router — liveness, 360° profile, timeline, compare, stats.

Handlers moved verbatim from api.py during the router split; paths and
response shapes are unchanged.
"""

import json
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api_shared import (
    _audit,
    config,
    get_profiler,
    ProfileRequest,
    CompareRequest,
)

router = APIRouter()


class TimelineRequest(BaseModel):
    query: str


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.post("/api/profile")
def profile(req: ProfileRequest):
    """Merchant 360° profile — everything the registry knows about a fragment.

    Accepts ANY fragment (name, email, phone, MX code, TID, account number…)
    and returns the seed match plus an aggregation across the whole entity
    family: every unique email/phone/TID/MX/address/contact, the distinct
    merchant-name variants, the sources, and the full linked rows.
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    _audit("profile", json.dumps({"query": query}))
    return get_profiler().build(query, max_members=req.max_members)


@router.post("/api/timeline")
def timeline(req: TimelineRequest):
    """Per-terminal timeline for a merchant fragment (build-time events).

    Accepts any fragment (name, phone, MX code, TID, account number) and
    resolves it to the terminal key(s) the registry actually stores, then
    returns every derived event from the merchant_events table:
      - first_seen / last_seen  (onboarding + most recent trace)
      - name_variant            every distinct name, with its source file
      - account_change          old->new transitions from the Change sheet

    Resolution is DB-grounded: keys only exist when the registry stores them.
    """
    from merchant_intelligence.enrich import keys_for_query, timeline_for
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    conn = sqlite3.connect(str(config.active_db()))
    conn.row_factory = sqlite3.Row
    try:
        keys = keys_for_query(conn, query, limit=8)
        out = []
        for field, key in keys:
            events = timeline_for(conn, key)
            if events:
                out.append({
                    "key_field": field,
                    "terminal_key": key,
                    "event_count": len(events),
                    "events": events,
                })
        return {"query": query, "resolved": len(out), "terminals": out}
    finally:
        conn.close()


@router.post("/api/compare")
def compare(req: CompareRequest):
    """Compare two merchants' 360° profiles side by side.

    Builds both profiles (any fragments work — name, email, phone, MX code,
    TID, account number…) and diffs them: which identifiers the two share,
    a per-field match/overlap/differ table, and how many family rows overlap.
    """
    query_a = req.query_a.strip()
    query_b = req.query_b.strip()
    if not query_a or not query_b:
        raise HTTPException(status_code=400, detail="query_a and query_b are required")
    return get_profiler().compare(query_a, query_b, max_members=req.max_members)


@router.get("/api/stats")
def stats():
    try:
        conn = sqlite3.connect(str(config.active_db()))
        total = conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
        conn.close()
    except Exception:
        total = 0
    return {"total_records": total}
