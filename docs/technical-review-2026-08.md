# Technical Review: Merchant Parameter Intelligence (codebase-grounded)

> **Revision note (2026-08-15):** this is a codebase-grounded revision of the
> external technical review. Every "current state" claim below was verified
> against the actual repo — files, symbols, tables, and counts. **Workstream 4
> has been rewritten per owner decision: a deterministic "LLM-like" copilot
> with no external LLM.** The original review text is preserved verbatim in
> `docs/technical-review-2026-08-original.md`. Where this document and the
> code disagree, the code wins.

---

## 1. Executive summary (grounded)

The repo is a merchant-search and investigation toolkit for the 2ISW / NNPC
merchant parameter files. Verified shape of the system:

- **Backend:** one FastAPI app, `api.py` (**1,675 lines, 44 routes**), fronted
  by a React/Vite app (`web/`) that proxies `/api` → `:8000`.
- **Engine:** a deterministic NLU intent parser in
  `merchant_intelligence/tasks/` (`engine.py`, `intents.py`, `parser.py`,
  `pipelines.py`, `db.py`, `models.py`, `vocab.py`), driven by **27 intents**
  in `intents.json` mapped to **28 pipeline functions** in `_PIPELINES`
  (`pipelines.py:1480`).
- **Data:** three SQLite databases under `data/` (`intelligence.db` 270 MB,
  `merchant_search.db` 212 MB, `merchant_intel.db` 186 MB), rebuilt by a
  four-step scripted pipeline (`app.start rebuild_databases()`). The active
  table `merchants` has **45 columns and 76,875 rows**, plus FTS5 + trigram
  indexes and auxiliary tables (`merchant_events` 150,079 rows,
  `name_buckets` 13,702 rows, `aliases` / `learned_mappings` / `search_history`
  currently empty).
- **Tests:** 12 test files (~4,223 lines), the flagship `tests/test_tasks.py`
  (2,480 lines) using a **custom `check()` harness — 593 checks**, not pytest.

The strategic thesis of the original review still stands: the valuable asset is
the deterministic engine, and the local-only, single-writer, unauthenticated
shape of the system is what limits it from becoming a team/regulated product.
The five workstreams below keep the original order (1→2→3→4→5) but are now
specified against the actual code.

## 2. Verified system facts (evidence appendix at §11)

| Area | Fact (verified) | Evidence |
|---|---|---|
| API surface | 44 routes, one file, unversioned | `api.py` route→handler map (§11) |
| Auth / audit | **None**: 0 hits for auth/login/session/audit/mask/rbac; only middleware is CORS | `api.py` scan (§11) |
| Sensitive data | `merchants` stores `bvn`, `account_number`, `static_acc_no`, `phone`, `email` | `PRAGMA table_info(merchants)` |
| Intents | 27 intent blocks in `intents.json` (+ `slang`) | `intents.json` top-level |
| Pipelines | 28 entries in `_PIPELINES`, each returns `{columns, rows, not_found, summary}` | `pipelines.py:1480` |
| Plan model | `TaskDescriptor` already carries `intents`, `clauses`, `workflow`, `references_previous`, `key_merchants` | `models.py:16` |
| Clarification | `top_two_gap()` + `suggest_clarification()`; thresholds fitted by `calibration.py` | `engine.py:682,705` |
| Self-improve seeds | `feedback.py`, `calibration.py`, `preferences.py`, `aliases.py` (AliasEngine), `scripts/self_improve.py` (recall@1 baseline gate) | module scans (§11) |
| Ops | `app.start` preflight + 4-step rebuild; `build_intelligence_db.py --watch` exists | `app.start`, `build_intelligence_db.py:784` |
| CI / packaging | **None**: no Dockerfile, no docker-compose, no `.github/workflows` | filesystem check |
| Observability | 0 OpenTelemetry/tracing refs in `api.py` | scan |

## 3. What the original review got right — and the two places it was off

Verified against the code, every headline claim holds:

1. **No auth/authz/masking/audit** — confirmed; the only `api_key` string is
   `LLM_API_KEY` for `/api/brief`.
2. **3 SQLite files, manual rebuild, denormalized schema, no migrations** —
   confirmed; the rebuild pipeline is `app.start rebuild_databases()`
   (rebuild_db.py → build_intelligence_db.py → sync_intel_db.py →
   self_improve.py).
3. **Monolith, no versioned API, no observability, Excel-first** — confirmed;
   **44 routes** (review estimated ~30) in `api.py`, 16 openpyxl references,
   0 `/api/vN` paths, 0 tracing.
4. **Closed-world parser; LLM relegated to `/api/brief`** — confirmed;
   **27 intents** (review estimated ~25); the LLM is a `urllib` call in
   `brief.py` plus an optional refinement hook in the engine
   (`_llm_configured`/`_llm_interpret`, `engine.py:474/478`).
5. **Self-improvement seeds exist but are manual/unmeasured** — confirmed.

Two corrections the review itself flagged as uncertain, now settled:

- **The engine is better-layered than assumed.** `merchant_intelligence/tasks/`
  is a clean, independently-testable package; several `api.py` handlers are
  thin wrappers over it (`task()` → `detect_task`/`execute_task`,
  `profile()` → `MerchantProfile`). Workstream 3 is mostly extracting
  route/formatting glue, not untangling business logic.
- **Partial lineage already exists** — `merchants.sheet_name` encodes
  `"file :: sheet"`, and `row_number` / `imported_at` / `raw_data` (full source
  row as JSON) are captured. What is missing is an ingestion-run model and
  cross-row provenance, making workstream 2 additive rather than greenfield.

Bonus finding while grounding: `AI_AGENT_CONTEXT.md` says the decisive-match
threshold is "~9.0", but `config.py:430` sets `DECISIVE_MATCH_THRESHOLD = 85`
(0–100 score scale) — the context file is stale; the code wins.

---

## 4. Workstream 1 — Enterprise Security & Compliance Foundation

### Current state (grounded)

- `api.py`'s 44 handlers take **no authentication dependency** — 0 uses of
  FastAPI `Depends`, `get_current_user`, or any auth helper. The only
  middleware registered is `CORSMiddleware` (localhost origins).
- The `merchants` table stores `bvn`, `account_number`, `static_acc_no`,
  `phone`, `email` in plaintext. `search_history` exists (`id, query,
  result_merchant, result_score, clicked`) but has **0 rows** and is a click
  log, not an audit trail. Request logs (`data/requests_log.jsonl`,
  `data/request_log.jsonl`) are plain JSONL, not append-only and not
  actor-attributed.
- Admin-like capabilities are already scattered across routes: alias
  moderation (`POST /api/aliases/approve|reject`), intent editing
  (`PUT /api/intents`), settings (`PUT|DELETE /api/settings`) — exactly the
  surface RBAC roles should gate.

### Approach rooted in the codebase

1. **AuthN/Z via a `security/` module** — add a package next to
   `merchant_intelligence/` exposing FastAPI dependencies; wire a per-router
   dependency into the existing 44 handlers (grouped per workstream 3's
   routers). Roles map 1:1 onto existing surfaces: Viewer = search/profile
   reads; Analyst = everything except export endpoints (`*/export` +
   `reconcile_export`); Administrator = `PUT /api/intents`,
   `PUT|DELETE /api/settings`, `POST /api/aliases/approve|reject`,
   `GET /api/calibration`.
2. **Masking at serialization, not the UI** — the 44 handlers currently
   return raw dicts; introduce a single response-shaping step (FastAPI
   response model / a `serialize()` wrapper) that truncates `bvn`,
   `account_number`, `static_acc_no`, `phone`, `email` for Viewer roles.
3. **Audit log** — a new append-only `audit_log` table (SQLite, matching the
   existing table conventions; the FTS5 no-UPSERT gotcha does not apply to
   regular tables). Hook it into the same chokepoints as masking, plus
   `task_export`/`search_export` and `/api/task` executions.
4. **Tenancy-ready user model** — a `users`/`roles` schema even with one
   tenant; `engine_settings.json`-style config keeps it tunable.

**Effort:** high (2–4 sprints) — but the chokepoints already exist (44 named
handlers, one DB module per layer), so it is additive wiring, not rework.

> **Status (workstream 1 shipped, opt-in):** (1) the immutable audit trail
> (`merchant_intelligence/audit.py`, `GET /api/audit`, wired into every
> search/profile/task/brief/batch/reconcile/export endpoint, Audit Trail UI
> page; `tests/test_audit.py` 18 checks). (2) AuthN/Z + RBAC + masking
> (`merchant_intelligence/auth.py`: pbkdf2 hashing, expiring persisted
> session tokens, viewer<analyst<administrator role matrix matching this
> section's role split, deep-walk masking of bvn/account/static/phone/email)
> enforced by an HTTP middleware in api.py, OFF by default with zero
> behavior change until enabled; session username becomes the audit actor;
> Login page + Rule Engine Access-control card; `tests/test_auth.py` 27
> checks). Remaining: tenancy-ready user model + KMS-grade encryption.

## 5. Workstream 2 — Governed Data Platform & Real-Time Ingestion

### Current state (grounded)

- Three SQLite DBs rebuilt wholesale by `app.start rebuild_databases()`:
  `scripts/rebuild_db.py` → `merchant_search.db`, `scripts/build_intelligence_db.py`
  → `intelligence.db`, `scripts/sync_intel_db.py` → `merchant_intel.db`, then
  `scripts/self_improve.py` runs the recall gate.
- `build_intelligence_db.py` already has the hard parts: auto header detection
  (`_detect_header_row`, `_borrow_reference_headers`, `read_sheet_detected`),
  stacked export blocks, exclusion of derived exports (`find_excel_files`),
  repair passes (`_repair_placeholder_names` at :856, `_resolve_code_names`
  at :756), and a **proof gate `verify_search()` at :968** that fails the build
  on regressions. A `--watch` mode exists (`watch_and_rebuild`, :784).
- No migrations, no versioned schema, no ingestion-run model. Freshness is
  "somebody ran the rebuild."

### Approach rooted in the codebase

1. **Staging → promote, not full rebuild.** Reuse `read_sheet_detected` +
   `_detect_header_row` to parse into a `staging` table; validate with the
   existing checks (schema, duplicates, header drift — the logic behind
   `verify_search()`); promote by delete-then-insert into `merchants` (the
   FTS5 no-UPSERT constraint already dictates this pattern in both build
   scripts). `watch_and_rebuild` becomes the incremental driver instead of a
   full replace.
2. **Lineage tables.** Add `source_files` and `ingestion_runs`; keep the
   existing `sheet_name`/`row_number`/`imported_at`/`raw_data` columns as the
   row-level provenance (already present).
3. **Schema versioning.** A `schema_version` table + a migration runner
   module (Alembic only if/when Postgres is adopted). Until multi-writer is
   needed, SQLite is sufficient — the constraint is the Excel→parse loop, not
   the storage engine.
4. **Keep FTS5.** The FTS5 + trigram indexes (76,875 rows) already serve
   search/autocomplete; no rewrite needed at this step.

**Effort:** high (3–5 sprints), but the parse/verify machinery is already
written — this is orchestration + staging around it.

## 6. Workstream 3 — Headless Intelligence API + Observability

### Current state (grounded)

- All 44 routes live in `api.py` (1,675 lines). Handler clusters are already
  identifiable by name: search/suggest/similar/autocomplete, profile/timeline/
  compare/entity, task/task-analyze/task-export, batch, quickmatch, reconcile,
  report, quality, aliases, intents/settings, feedback/calibration/preferences,
  brief/selfimprove.
- Exports are separate endpoints (7 of them) returning styled xlsx — the API
  is already **JSON-first with Excel as an explicit export transform**, which
  is exactly the target shape; the review's "Excel as primary output" concern
  is really "exports are first-class citizens", not "the API is Excel-only".
- 0 versioning, 0 OpenTelemetry, 0 CI, 0 Docker. The only ops automation is
  `app.start`'s preflight (venv, deps, DB exists, `node_modules`, frontend
  sanity script `web/scripts/check-export-name.mjs`, port checks).

### Approach rooted in the codebase

1. **Routers over the existing seams.** `APIRouter`s: `search`, `profile`,
   `tasks`, `export`, `admin`. The service layer already exists —
   `merchant_intelligence/tasks/` (`detect_task`, `execute_task`,
   `suggest_clarification`, `analyze`) and the profile/entity/quality modules.
   Endpoint paths and response shapes stay identical; the frontend and the 593
   checks keep passing.
2. **Versioned contract.** Mount routers under `/api/v1` with the current
   paths as aliases; OpenAPI is already generated by FastAPI — keep it as the
   contract of record.
3. **Observability.** Wrap the same chokepoints as workstream 1: per-request
   span for `detect_task`→`execute_task` (the multi-step pipeline is the
   highest-value trace), DB query timing, latency/recall/intent-confusion
   metrics. Structured logs replace the JSONL debug files over time.
4. **CI + packaging.** GitHub Actions runs the existing suite — note the
   harness is **not pytest**: `python tests/test_tasks.py` (+ the other 11
   files), plus `scripts/build_intelligence_db.py`'s `verify_search()`.
   Dockerfile for `api.py` (uvicorn) and the Vite build; docker-compose dev
   profile. This is what turns the 593 checks into a regression shield.

**Effort:** medium (2–3 sprints) — the decoupling is extraction, not redesign.

## 7. Workstream 4 — Deterministic "LLM-like" Copilot (no external LLM)

> Rewritten per owner decision. The goal is an experience that feels like an
> LLM copilot — natural language in, multi-step investigation, prose answer —
> built **entirely** on the deterministic engine. No external model, no
> reasoning dependency.

### Current state (grounded): the engine already does most of it

- **Intent + parameter extraction:** `detect_task` (`engine.py:51`) →
  `TaskDescriptor` with `confidence` (0–100), `identifiers`, `names`,
  `clauses` (per-intent identifier attachments), `segment`, `params`,
  `key_merchants`.
- **Compound requests:** `TaskDescriptor.intents` + `_merge_tables`
  (`pipelines.py:1512`) run multiple pipelines and merge into one table.
- **Clause attachment:** `split_clauses` / `_clause_scope` — "get email for
  A **and** phone for B".
- **Workflow planning:** `TaskDescriptor.workflow` already models a
  dependency-aware plan — `steps: [{intent, step, requires,
  resolved_internally, produces}]` — built for the UI/debugging, **not yet
  executed**.
- **Multi-turn reference:** `remember_entities` / `last_entities` /
  `inherit_reference` (`engine.py:408/423/440`) resolve "…for the above
  merchant" against the last request.
- **Disambiguation:** `top_two_gap` + `suggest_clarification` with thresholds
  fitted by `calibration.py`; "remember my choice" persistence in
  `preferences.py`.
- **Natural-language output:** `brief.py` already has a fully offline
  **deterministic template brief** (`build_template_brief`, `_red_flags`,
  `_identity_lines`) that turns a profile into prose.
- **Next steps:** `suggest_next_steps` (`engine.py:528`).
- An optional LLM refinement hook exists (`_llm_configured`/`_llm_interpret`,
  `engine.py:474/478`) — it stays as an additive enhancement only, never the
  default path.

### The gap

A request is interpreted and executed **once**; the `workflow` plan is shown,
not run; multi-step investigations ("static account for MX141692, then the
phone on that account") require the human to re-paste. That is the entire
delta between this engine and a "copilot".

### Approach (deterministic, no LLM)

1. **Execute the existing plan.** Make `TaskDescriptor.workflow` executable:
   a `plan_runner` that walks `steps` in dependency order, feeds each step's
   `produces` into the next step's `requires`, and calls the existing
   `_PIPELINES` functions. The plumbing is already there (`resolve_any`,
   `static_accounts_for_mx`, `static_accounts_for_acc` in `tasks/db.py`) —
   what's missing is a chaining executor, e.g. "resolve MX → run
   `static_account` pipeline → take first account → run `phone` pipeline".
2. **Session context.** Promote `remember_entities`/`last_entities` from
   last-request globals to a per-session store so a copilot thread can chain
   "…and their phone" over several turns.
3. **Prose answers.** Generalize `build_template_brief`'s pattern into a
   `explain_result(task, result)` renderer that converts any pipeline result
   (`{columns, rows, not_found, summary}`) into a short deterministic
   sentence/paragraph — the "analyst's answer" without a model.
4. **Replayable traces.** `TaskDescriptor` is already JSON-serializable —
   persist `{request, descriptor, workflow, result}` per session and expose
   re-run via the existing `/api/task`; an investigation becomes a recorded,
   replayable trace (the same artifact the original review wanted from an
   agent, minus the LLM).
5. **Safety rails stay deterministic.** Confidence gates from
   `calibration.py` (ask/gap thresholds), the existing clarification flow, and
   human-in-the-loop for export endpoints (workstream 1's RBAC).

**Effort:** high but bounded (2–4 sprints) — it is mostly the `plan_runner`,
session store, and `explain_result`, all over existing pipelines.

## 8. Workstream 5 — Self-Improving Intelligence Flywheel

### Current state (grounded)

The seeds are real and verified:

- `feedback.py` — `data/requests_log.jsonl` request log, `mine_patterns()`
  with a 3-sample guard, `apply_pattern()` that **writes accepted patterns
  into `intents.json`**, rejection list in `data/suggestions_rejected.json`.
- `calibration.py` — `data/request_log.jsonl`, banded-acceptance fitter for
  `ask_threshold` / `gap_threshold` consumed by `suggest_clarification`.
- `aliases.py` — `AliasEngine` auto-learning → `data/merchant_aliases.json`,
  moderated via `/api/aliases` + approve/reject; `MANUAL_ALIASES` from
  `config.py` / `data/manual_aliases.json`.
- `preferences.py` — phrase→intent store.
- `scripts/self_improve.py` — alias-free recall harness with a **baseline gate**
  (`data/alias_free_baseline.json`, recall@1/recall@3, regression fails the
  run) — already runs after every rebuild.
- `enrich.py` — `compute_quality` + `/api/quality` quality scan.
- `golden.py` (213 lines) — benchmark golden set.

What's missing: **CI gating** (no CI), a **central ground-truth store**
(`search_history`/`aliases`/`learned_mappings` tables exist but are empty;
corrections live in JSONL files), **versioned learned assets** (intents.json /
alias JSONs are edited in place, not diffed or reviewed), and **scheduled
drift monitoring** (quality scan exists but nothing schedules or alerts).

### Approach rooted in the codebase

1. **CI gate (from workstream 3).** Run `self_improve.py`'s recall gate + the
   593 checks + `verify_search()` on every push. Extend the golden set
   automatically from the feedback log.
2. **Ground truth in DB, not JSONL.** Promote corrections, alias approvals,
   and clarification outcomes into the **already-existing** `aliases`,
   `learned_mappings`, and `search_history` tables (currently 0 rows) with
   `ingested_at`/`actor` columns (workstream 1's audit hooks). Mine them into
   (a) `intents.json` pattern updates via `apply_pattern`, (b) alias/entity
   updates via `AliasEngine`, (c) calibration re-fits.
3. **Governed learned assets.** Git-track `intents.json`, `merchant_aliases.json`,
   `manual_aliases.json`, calibration thresholds; the existing Alias Review
   page (`/api/aliases` + approve/reject) becomes the governance UI.
4. **Drift monitoring.** Schedule `compute_quality`/`/api/quality` (e.g. via
   the `--watch`/scheduler pattern already in `build_intelligence_db.py`) with
   alerts when freshness or match rates degrade.

**Effort:** high (4–6 sprints); depends on workstreams 1, 2, 3 (audit capture,
central store, CI/telemetry).

---

## 9. Execution roadmap (1 → 2 → 3 → 4 → 5)

The original ordering is kept and now has concrete enablers:

1. **Security first** — 44 named handlers + serialization chokepoints make the
   auth/masking/audit wiring additive; audit capture feeds workstream 5.
2. **Data platform second** — the parse/verify machinery already exists;
   staging+promote and lineage tables turn `watch_and_rebuild` into the
   incremental driver.
3. **API + observability third** — routers over the existing engine layer;
   CI turns the 593 checks + recall gate into a regression shield.
4. **Deterministic copilot fourth** — executes the `workflow` plan the engine
   already builds; no LLM, no reasoning dependency; produces replayable traces
   that feed workstream 5.
5. **Flywheel last, never finishes** — with audit, central store, CI, and
   traces in place, the system enters permanent, measurable self-improvement.

Because workstream 4 no longer depends on an external model, it can overlap
workstream 3 (same service layer, same routers) — the two de-risk each other.

## 10. 12–24 month vision (no-LLM copilot)

A governed merchant intelligence platform where a compliance-audited API
serves the NNPC/2ISW ecosystem, a **deterministic** copilot resolves
multi-step investigations from a single natural-language request (replayable,
auditable, offline), and a measured feedback flywheel compounds accuracy —
all with the engine that exists today as the core, no external reasoning
dependency.

---

## 11. Verification log (2026-08-15, read-only)

| Claim | Evidence |
|---|---|
| `api.py` = 1,675 lines, 44 routes | line count; decorator scan — full route→handler map: `health`, `profile`, `timeline`, `compare`, `stats`, `search`, `entity`, `search_export`, `idclass_debug`, `autocomplete`, `suggest`, `similar`, `duplicates`, `aliases`, `alias_approve`, `alias_reject`, `report`, `report_export`, `learn`, `quickmatch`, `quickmatch_export`, `task`, `feedback_suggestions`, `suggestion_apply`, `suggestion_reject`, `task_analyze`, `get_calibration`, `reset_calibration`, `get_preferences`, `forget_preference`, `get_intents`, `update_intent`, `get_settings`, `update_settings`, `reset_settings`, `task_export`, `batch`, `batch_export`, `quality`, `quality_export`, `reconcile_endpoint`, `brief`, `selfimprove_status`, `reconcile_export` |
| No auth/audit/masking | `api.py` regex scan: auth 0, login 0, session 0, Authorization 0, Bearer 0, password 0, audit 0, mask 0, rbac 0, role 0, permission 0, secret 0, encrypt 0, `Depends` 0; only `CORSMiddleware`; `token` hits = fuzzy `token_sort_ratio` |
| Sensitive columns | `PRAGMA table_info(merchants)`: 45 cols incl. `bvn`, `account_number`, `static_acc_no`, `phone`, `email`, `raw_data`, `imported_at`, `onboarded_date`, `bank_code`, `quality_score` |
| DB shape | `intelligence.db` tables: `merchants` (76,875), `merchants_fts`(+trigram), `merchant_events` (150,079), `name_buckets` (13,702), `aliases` (0), `learned_mappings` (0), `search_history` (0) |
| 27 intents | `intents.json` `intents` dict keys (27): account_name, account_number, address, alias, bank, beneficiary, change_details, compare, contact, count, coverage, duplicates, email, formerly, mxcode, onboarded, payable, phone, profile, related, source, state, static_account, summary, tid, top, verify + `slang` |
| 28 pipelines | `_PIPELINES` at `pipelines.py:1480`; `_merge_tables` at :1512; each pipeline returns `{columns, rows, not_found, summary}` |
| TaskDescriptor | `models.py:16` — fields incl. `intents`, `identifiers`, `named`, `names`, `clauses`, `excluded`, `workflow` (`steps: [{intent, step, requires, resolved_internally, produces}]`), `references_previous`, `key_merchants`, `confidence` |
| Engine functions | `engine.py`: `detect_task` :51, `remember_entities` :408, `last_entities` :423, `inherit_reference` :440, `_llm_configured` :474, `_llm_interpret` :478, `suggest_next_steps` :528, `execute_task` :586, `top_two_gap` :682, `suggest_clarification` :705, `analyze` :841 |
| Parser functions | `parser.py`: `looks_like_address` :198, `key_merchant_matches` :105, `extract_segment` :157, `parse_identifiers` :342, `parse_named_identifiers` :357, `extract_params` :432, `extract_compare_pair` :464, `split_clauses` :493, `extract_names` :519 |
| DB helpers | `tasks/db.py`: `resolve_mx` :46, `resolve_any` :73, `static_accounts_for_acc` :110, `static_accounts_for_mx` :135 |
| Vocab constants | `vocab.py`: `_DEFAULT_INTENT_PATTERNS` :35, `_DEFAULT_SLANG` :309, `_DEFAULT_INTENT_KEYWORDS` :324, `_INTENT_CONFIG = reload_intents()` :393, `CHAINABLE` :486, `NAME_CAPABLE_INTENTS` :508, `NAME_STOP_WORDS` :520, `SEGMENT_FIELDS` :635, `SEGMENT_COLLECTIVE` :657, `NAME_ANCHORS` :915 |
| LLM scope | `brief.py`: `llm_available` :59, `_call_llm` :64 (urllib → `LLM_BASE_URL`, default api.openai.com/v1), `build_template_brief` :143, `build_brief` :200; `api.py` 1 `LLM_API_KEY` ref |
| Self-improve seeds | `feedback.py`: `log_request` :205, `mine_patterns` :374, `reject` :412, `apply_pattern` :432; logs at `data/requests_log.jsonl`; `calibration.py`: `record` :93, thresholds fitted from `data/request_log.jsonl`; `preferences.py`: `learn` :120, `lookup` :130; `aliases.py`: `AliasEngine` :17, `ALIAS_CACHE_FILE = data/merchant_aliases.json` (`config.py:49`); `scripts/self_improve.py`: `run_alias_free` :46, baseline `data/alias_free_baseline.json`; `enrich.py`: `compute_quality` :154, `build_events` :314 |
| Thresholds | `config.py`: `DECISIVE_MATCH_THRESHOLD = 85` :430, `EXACT_MATCH_THRESHOLD = 95` :414, `HIGH_CONF_THRESHOLD = 80` :415, `POSSIBLE_THRESHOLD = 50` :416, `BANK_NAME_BOOST = 6.0` :438, `MANUAL_ALIASES` :329 |
| Rebuild pipeline | `app.start rebuild_databases()`: rebuild_db.py → merchant_search.db; build_intelligence_db.py → intelligence.db; sync_intel_db.py → merchant_intel.db; self_improve.py (recall gate). Preflight checks venv/deps/DB/node_modules/`web/scripts/check-export-name.mjs`/ports |
| Build script machinery | `build_intelligence_db.py`: `_detect_header_row` :155, `_borrow_reference_headers` :190, `read_sheet_detected` :332, `_repair_placeholder_names` :856, `_resolve_code_names` :756, `verify_search` :968, `watch_and_rebuild` :784 |
| Tests | 12 files, ~4,223 lines; `test_tasks.py` 2,480 lines, custom `check()` harness (PASS/FAIL counters), 593 checks, live-API `[5*]` sections; `[4h]` config==defaults parity test |
| No CI/Docker/otel | `Dockerfile`/`docker-compose*`/`.github/workflows/*` all absent; opentelemetry/tracing 0 hits in `api.py` |

---

## 12. Known doc-vs-code discrepancies (code wins)

- `AI_AGENT_CONTEXT.md` says decisive-match threshold "~9.0" → code says 85
  (`config.py:430`).
- `AI_AGENT_CONTEXT.md` said the shell lacks grep/tail/sleep → tools are now
  installed via Scoop (`~/scoop/shims`) with a PATH-export caveat.
- Original review estimated ~30 routes / ~25 intents → actual 44 / 27.
