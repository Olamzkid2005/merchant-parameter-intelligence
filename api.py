"""api.py — FastAPI backend for the Merchant Intelligence React frontend.

Bootstrap only: the app + middleware live here, while the handlers live in
domain routers under api_routes/ (search, profile, tasks, auth, admin) with
their shared helpers/models in api_shared.py. This is the roadmap #3
"headless intelligence API" split — every path and response shape is
unchanged, so the frontend and the live-API test suites keep working.

For backwards compatibility (tests do `import api; api.search(...)` /
`api.SearchRequest`), every handler and request model is re-exported here at
module level.

Endpoints
---------
GET  /api/health          — liveness probe
GET  /api/stats           — total record count
POST /api/search          — {query, limit} → scored results
POST /api/batch           — {merchants: [...]} → best-match rows
POST /api/batch/export    — same as /api/batch but returns an .xlsx file
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json  # noqa: E402  (after sys.path setup)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

from api_shared import (  # noqa: E402
    _audit,
    _key_merchants_for,
    _log_task_request,
    _multi_identifier_query,
    _quick_match_rows,
    _search_with_multi,
    _style_workbook,
    config,
    get_profiler,
    get_resolver,
    get_searcher,
    BatchRequest,
    CompareRequest,
    CopilotRequest,
    EntityRequest,
    LearnRequest,
    ProfileRequest,
    QuickMatchRequest,
    SearchRequest,
    TaskRequest,
)

from api_routes.auth_routes import (  # noqa: E402
    AuthConfigRequest,
    AuthPasswordRequest,
    AuthUserRequest,
    LoginRequest,
    router as auth_router,
)
from api_routes.profile_routes import (  # noqa: E402
    TimelineRequest,
    router as profile_router,
)
from api_routes.search_routes import (  # noqa: E402
    AliasAction,
    router as search_router,
)
from api_routes.tasks_routes import (  # noqa: E402
    IntentPattern,
    IntentUpdateRequest,
    PreferenceForgetRequest,
    SettingsUpdateRequest,
    ShadowReviewLabelRequest,
    SuggestionAction,
    SynonymApplyRequest,
    SynonymStatusRequest,
    router as tasks_router,
)
from api_routes.admin_routes import (  # noqa: E402
    router as admin_router,
)

app = FastAPI(title="Merchant Intelligence API", version="1.0.0")

# Allow the Vite dev server to call the API directly (a Vite proxy is also
# configured, but CORS keeps the door open for other origins).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Opt-in authN/Z + field masking (roadmap #1, slice 2). When access
    control is disabled (the default) every request passes through
    untouched — the desktop-tool experience is unchanged."""
    from merchant_intelligence import auth
    if not auth.enabled():
        return await call_next(request)
    path = request.url.path
    if path in auth.EXEMPT_PATHS:
        return await call_next(request)
    token = request.cookies.get("mi_session")
    session = auth.get_session(token) if token else None
    if not session:
        return JSONResponse(status_code=401,
                            content={"detail": "authentication required"})
    if not auth.require(path, request.method, session["role"]):
        return JSONResponse(status_code=403,
                            content={"detail": "insufficient role for this action"})
    auth._current_actor.set(session["username"])
    resp = await call_next(request)
    # Field-level masking at the API boundary for viewer sessions.
    if session["role"] == "viewer" and "application/json" in (
            resp.headers.get("content-type") or ""):
        body = b"".join([chunk async for chunk in resp.body_iterator])
        # Drop length/encoding headers — the masked body has a different
        # size; letting Response recompute content-length avoids truncated
        # reads (IncompleteRead) on the client.
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in ("content-length", "content-encoding")}
        try:
            masked = json.dumps(auth.mask_payload(json.loads(body)),
                                default=str).encode("utf-8")
            return Response(content=masked, status_code=resp.status_code,
                            headers=headers, media_type="application/json")
        except Exception:  # noqa: BLE001 — never break a viewer response
            return Response(content=body, status_code=resp.status_code,
                            headers=headers, media_type=resp.media_type)
    return resp


# Mount the domain routers TWICE over the same handlers (roadmap #3 slice):
#   /api      — the legacy surface, byte-identical paths as before the split
#   /api/v1   — the versioned contract, same handlers, stable for consumers
# Registration order matches the original api.py route order (health/auth
# first, then profile/search/tasks/admin) so route matching is unchanged.
app.include_router(auth_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")


# ── backwards-compatible re-exports ──────────────────────────────────────
# Legacy imports (`import api; api.search(...)`, `api.SearchRequest`, …) keep
# resolving through this module — the handlers run from the routers, these
# names are just aliases so nothing downstream changes.

from api_routes.profile_routes import health  # noqa: E402,F401
from api_routes.profile_routes import (  # noqa: E402,F401
    compare, profile, stats, timeline,
)
from api_routes.search_routes import (  # noqa: E402,F401
    alias_approve, alias_reject, aliases, autocomplete, duplicates, entity,
    idclass_debug, search, search_export, similar, suggest,
)
from api_routes.tasks_routes import (  # noqa: E402,F401
    apply_synonyms, audit_endpoint, copilot, feedback_suggestions,
    forget_preference, get_calibration, get_intents, get_preferences,
    get_settings, get_synonym_candidates, ingest_endpoint, propose_synonyms,
    reset_calibration, reset_settings, shadow_review, shadow_review_label,
    suggestion_apply, suggestion_reject, synonym_manifest, synonym_status,
    task, task_analyze, update_intent, update_settings,
)
from api_routes.admin_routes import (  # noqa: E402,F401
    batch, batch_export, brief, learn, quality, quality_export, quickmatch,
    quickmatch_export, reconcile_endpoint, reconcile_export, report,
    report_export, selfimprove_status, task_export,
)
from api_routes.auth_routes import (  # noqa: E402,F401
    auth_add_user, auth_config, auth_login, auth_logout, auth_me,
    auth_remove_user, auth_reset_password, auth_save_config,
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
