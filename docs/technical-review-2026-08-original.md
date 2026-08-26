# Technical Review: merchant-parameter-intelligence

> External technical review of this repository, captured August 2026.
> The review's code-level claims were subsequently verified against the
> actual codebase in a due-diligence pass — see **Appendix A** for the
> claim-by-claim results.

## Assessment basis

The repo's documentation (README + API surface) is unusually rich and
self-describing, so this review is grounded in the documented architecture.
Two caveats that shape everything below: (1) raw source fetches were blocked,
so code-level claims are inferences from docs; (2) the current design bakes in
a hard assumption — "all data lives locally; nothing leaves the machine" —
which is simultaneously its security model, its scalability ceiling, and its
competitive trap.

## Executive Summary

This is a genuinely impressive solo build: a fuzzy-search + entity-resolution +
NLU-intent engine over 70K+ merchant records, with self-improvement hooks, a
30+ endpoint API, a real frontend, and 590+ tests. The problem is that it's
architected like a personal investigation workstation, while the domain
(NNPC/2ISW merchant parameter files: BVN, static accounts, phones, addresses)
is inherently a team-scale, regulated, intelligence product.

The strategic thesis: transform a local tool into a governed, AI-native
merchant intelligence platform — where the engine is an embeddable service,
data is centralized and streamed, security is real, and an agentic copilot
does the investigation while a feedback flywheel makes it smarter every day.

| # | Improvement | Type | Complexity |
|---|---|---|---|
| 1 | Enterprise Security & Compliance Foundation | Foundation | High |
| 2 | Governed Data Platform & Real-Time Ingestion | Upgrade + new capability | High |
| 3 | Headless Intelligence API + Observability | Platform-ification | Medium |
| 4 | Agentic "Merchant Copilot" (LLM + RAG) | New strategic direction | Very High |
| 5 | Self-Improving Intelligence Flywheel (MLOps) | New strategic direction | High |

## 1. Enterprise Security & Compliance Foundation

**Current limitation:** There is no authentication, authorization, masking, or
audit trail. Today, security is the architecture ("nothing leaves the
machine"). The moment the data is centralized or the app is deployed on a
network, every BVN and account number in the system is unprotected — a
catastrophic exposure.

**Why it matters:** This is the gate that decides whether the product can ever
be sold or deployed in a real organization. NDPR (Nigeria) and general
financial-data expectations require access control, field-level masking
(partial BVN/account display), and an immutable record of who queried what and
when. You cannot bolt this on after the fact — it must be designed into the
data model and API layer from day one.

**Proposed solution:**

- AuthN/Z: OIDC-compatible identity (enterprise SSO/ADFS, or a lightweight
  identity provider), with RBAC roles — e.g., Viewer (masked data), Analyst
  (full search, no export), Administrator (settings, alias approval, audit
  access).
- Field-level masking + encryption: masking rules at the API boundary (not the
  UI), KMS-backed encryption at rest and TLS in transit.
- Immutable audit log: every search, profile view, export, and intent
  execution logged with actor, timestamp, and scope (a dedicated `audit_log`
  table, append-only).
- Tenancy-ready user model even if only one tenant exists today.

**Expected benefits:** unlocks multi-user and multi-team deployment; de-risks
the data platform (#2); establishes the trust surface required for enterprise
sales and compliance review.

**Complexity:** High. Estimated effort: 2–4 sprints.

**Architecture considerations:** Security must live in a middleware/service
layer, not scattered across `api.py` handlers. Introduce a `security/` module
with decorators/dependencies; mask at serialization time via a single
response-shaping layer — this directly prepares the API refactor in #3.

*Status (shipped, opt-in):
1. Immutable audit trail — `merchant_intelligence/audit.py` (dedicated
   `data/audit_log.db`, append-only by construction, INSERT-only write
   path; survives merchant-DB rebuilds), `GET /api/audit` (entries +
   per-action stats), wired into every search, profile view, intent
   execution, brief, batch/reconcile, and export endpoint, and an Audit
   Trail page in the UI. `tests/test_audit.py` (18 checks).
2. AuthN/Z + RBAC + field-level masking — `merchant_intelligence/auth.py`
   (pbkdf2 password hashing, expiring opaque session tokens persisted in
   `data/sessions.json`, role matrix viewer < analyst < administrator
   mirroring the review's exact surface split, deep-walk field masking of
   bvn/account_number/static_acc_no/phone/email). Enforced by an HTTP
   middleware in api.py, OPT-IN (default off — the desktop tool is
   byte-for-byte unchanged until `enabled` is set). Session username flows
   into the audit trail as the actor. `GET/POST /api/auth/me|login|logout|
   config|users|password`; UI: Login page gate + Access-control card on
   the Rule Engine page. `tests/test_auth.py` (27 checks). Remaining:
   tenancy-ready `users`/`roles` schema and KMS-grade encryption.*

## 2. Governed Data Platform & Real-Time Ingestion

**Current limitation:** Three SQLite files (`merchant_search.db`,
`intelligence.db`, `merchant_intel.db`) rebuilt by manually dropping Excel
workbooks into `data/` and running `app.start rebuild`. The schema is a single
denormalized `merchants` table, `sheet_name` doubles as source attribution, and
there are no migrations or versioned schema. Data freshness = "somebody
remembered to rebuild."

> **Status (first slice shipped):** the ingestion ledger + freshness signal
> (`merchant_intelligence/ingest_ledger.py`, append-only `data/ingest_ledger.db`
> surviving merchant-DB rebuilds; every `app.start rebuild` and
> `build_intelligence_db.py` run is recorded with its per-source snapshot and
> row count; `GET /api/ingest` exposes runs/stats/freshness; Data freshness &
> ingestion ledger card on the Audit Trail page). "Somebody remembered to
> rebuild" is now visible: the card flags every workbook that is NEW or
> CHANGED since the last good build.
> **Watch mode shipped:** `merchant_intelligence/watcher.py` — a daemon thread
> in the API process polls freshness every 30s (env-tunable
> `INGEST_WATCH_INTERVAL/_SETTLE/_COOLDOWN`, `INGEST_WATCH=0` disables) and
> auto-rebuilds all three databases when a source drifts: settle-debounce
> (file untouched ≥20s) → cooldown (≥10min between rebuilds) → closes the
> API's cached DB connections (Windows file locks) → runs the same 3-script
> pipeline as `app.start rebuild` → resets the singletons so the next request
> reconnects to the fresh DBs. Endpoints `GET /api/ingest/watch` +
> `POST /api/ingest/watch/trigger`; "Scan & rebuild now" button + live watch
> state on the Audit Trail card. Ledger keys are case-normalized
> (`os.path.normcase`) so launch-path casing (run.bat vs shell cd) can't make
> every source read as permanently "new", and app-generated export workbooks
> (`EXCLUDED_EXPORTS`) are skipped by the freshness scan so they can never
> trigger a rebuild loop. `tests/test_watcher.py` (30 checks, hermetic).
> **Schema versioning + migrations shipped:** `merchant_intelligence/migrations.py`
> — native `PRAGMA user_version` tracking + an ordered, append-only migration
> registry (v1 baseline, v2 data-platform tables: source_files / identifiers /
> entity_clusters / data_quality_log). Re-applied after every rebuild (both the
> app.start pipeline and the watcher's steps end with it) and best-effort at API
> startup, so rebuilds can no longer silently drop non-build-script tables.
> Auth/tenancy + encryption tables stay behind the explicit /api/schema/migrate
> endpoint by design. `GET /api/ingest/watch` reports per-DB schema versions.
> `tests/test_migrations.py` (21 hermetic checks) in CI. Also fixed the
> recurring vocab.py double-escape regression at its root: test_feedback.py was
> writing a mis-escaped pattern and regenerating the REAL vocab.py — both
> config seams are now enforced. Remaining roadmap: source lineage.

**Why it matters:** The Excel → rebuild flow is the operational bottleneck and
the source of every data-quality bug (the README itself warns "check the
workbook first, then the build script, then the DB"). It's also single-writer:
no concurrent ingestion, no incremental updates, no lineage. A real
intelligence platform needs a source of truth that is always current and
provably correct.

**Proposed solution:**

- Migrate to Postgres (or a managed equivalent) with versioned migrations
  (Alembic-style) and a normalized model: `merchants`, `identifiers`,
  `source_files`, `ingestion_runs`, `audit_log`, `entity_clusters`.
- Replace full rebuilds with incremental, change-data-capture ingestion: watch
  the `data/` folder; on file change, parse → stage → validate (schema,
  duplicates, header drift) → promote to the active tables. Full-rebuild
  becomes an exceptional, logged operation, not the norm.
- Add lineage: every row records source_file → sheet → row_number →
  ingested_at → ingestion_run_id.
- Keep FTS5-style full-text search working via Postgres FTS or a dedicated
  search index.

**Expected benefits:** always-fresh data without manual ops; a validation gate
that catches bad workbooks before they corrupt the index; the substrate that
makes real-time agent queries (#4) trustworthy; migration path to multi-writer
and cloud.

**Complexity:** High. Estimated effort: 3–5 sprints.

**Architecture considerations:** This is where the "staging → promote" gate
(think dbt-style quality contracts) and the event stream (Debezium/Kafka or a
simpler outbox pattern) get introduced. It also creates the event telemetry
that powers the observability in #3 and the feedback loop in #5.

## 3. Headless Intelligence API + Observability

**Current limitation:** All ~30 endpoints live in one `api.py`, tightly coupled
to the React frontend, exporting to Excel as the primary output format. There
is no versioned API contract, no programmatic consumer story, and no
instrumentation — logs go to files, and debugging multi-step intent pipelines
is a manual exercise.

**Why it matters:** The engine is the valuable asset, not the UI. Fraud teams,
reconciliation pipelines, and support systems in the NNPC/2ISW ecosystem need
to call investigate, reconcile, and resolve from their own tools. A headless,
versioned API converts a desktop tool into Intelligence-as-a-Service — the
difference between "a nice app" and "an embeddable platform capability."

**Proposed solution:**

- Decouple: split `api.py` into routers (search, profile, tasks, admin,
  export) over a thin service layer; the engine package becomes a library with
  a stable internal interface.
- Versioned API contract: `/api/v1` (stable) alongside `/api/v2` (agentic,
  from #4); OpenAPI as the contract of record; JSON-first responses with Excel
  as an explicit export transform rather than the primary shape.
- OpenTelemetry end-to-end: tracing on every request, intent execution, and DB
  query; metrics on latency, recall, intent-confusion, and ingestion health;
  structured logs.
- Packaging & delivery: Docker images for API + web, a docker-compose dev
  profile, and CI (GitHub Actions) that runs the existing 590+ tests on every
  push — turning the strong test suite into a regression shield.

**Expected benefits:** external integrations become possible; the API becomes a
product boundary that can be sold/embedded; CI + tracing are prerequisites for
safely shipping agents (#4) and measuring the flywheel (#5).

**Complexity:** Medium. Estimated effort: 2–3 sprints.

> **Status (slices 1–2 shipped):** `api.py` (2,073 lines, 61 handlers) has
> been split into domain routers over a shared layer — `api_shared.py`
> (helpers/models/singletons) + `api_routes/` (`auth_routes`, `profile_routes`,
> `search_routes`, `tasks_routes`, `admin_routes`). `api.py` is now a slim
> bootstrap (app + CORS + security middleware) that mounts the routers and
> re-exports every handler/model so legacy `import api; api.search(...)` calls
> keep working. **The versioned contract is live**: the routers are mounted
> twice over the same handlers — `/api` (legacy, byte-identical paths) and
> `/api/v1` (stable for consumers; same 55 paths, verified mirror). The
> security middleware, audit actor, and masking apply to both surfaces
> automatically. Verified: OpenAPI path set byte-identical to the pre-split
> route set on both mounts (0 dropped, 0 added), full live-API suite green;
> `tests/test_api_split.py` (19 hermetic checks) locks both parities in.
> Remaining roadmap: OpenAPI-as-contract + JSON-first envelope with Excel as
> an explicit export transform, OpenTelemetry tracing/metrics, Docker
> packaging.

**Architecture considerations:** This is the moment to introduce the shared
response/error envelope, masking-aware serializers (inheriting from #1), and
idempotent job semantics for long-running tasks (batch/reconcile). It
deliberately reuses #1's security layer and #2's query layer — proof of the
compounding design.

## 4. Agentic "Merchant Copilot" (LLM + RAG) — New Strategic Direction

> **Status (first slice shipped):** the compound-request copilot
> (`merchant_intelligence/copilot.py`, `POST /api/copilot`, dedicated Copilot
> page in the UI). Hybrid NLU per the phased plan: when `LLM_API_KEY` is set
> the model proposes the step decomposition; otherwise (or on failure) the
> rule engine decomposes (whole-task → clause split). Every step is executed
> by the DETERMINISTIC engine (`detect_task`/`execute_task`/search) — the LLM
> can never inject identifiers or bypass a pipeline, and a chain like "find
> MEDPLUS then the static account for those" resolves "those/the above"
> against the previous step's output (`remember_entities` + `inherit_reference`
> + pronoun normalization). The response is the recorded, replayable trace:
> ordered plan + per-step results + provenance (mode/model/elapsed), audit-
> logged. Remaining slices: RAG grounding over the #2 data platform, LLM
> tool-use over the full #3 API (compare/reconcile/brief), and the
> trace→dataset feed into #5.

**Current limitation:** The intent parser is an impressive
regex/fuzzy/weighted-pattern engine (`intents.json` + `parser.py` +
`pipelines.py`), but it is fundamentally closed-world: it can only execute the
~25 hand-written intents, and every new phrasing or compound request is a new
pattern to tune. The LLM is relegated to a single `/api/brief` endpoint that
reports after the fact — the most powerful capability in the stack is used
last.

**Why it matters:** The product's differentiator is natural-language
investigation of messy merchant data. Regex ceilings are real: "merchants in
Lagos sharing an identifier with flagged TID 12345, excluding SPAR" is a
multi-step reasoning task that today requires a human to decompose into several
searches. The LLM should be the investigation, with the deterministic engine as
its tools — not the other way around.

**Proposed solution:**

- Hybrid NLU: LLM classifies intent and extracts parameters; the existing
  deterministic parser runs in parallel as a guardrail and fallback (the LLM
  is fallible; the rule engine is not). The existing clarification flow and
  "remember my choice" preferences become part of the agent loop.
- RAG over the merchant index: retrieval over the #2 data platform so the
  copilot answers with grounded, citeable facts ("Matched Address" column style
  provenance everywhere).
- Tool-using agent: give the model tool access to the #3 API (search, profile,
  compare, reconcile), with structured intermediate plans that are inspectable
  and re-runnable — an investigation brief becomes a recorded, replayable
  trace, not a one-shot answer.
- Safety rails: confidence thresholds before acting, human-in-the-loop for
  destructive/export operations, and the audit log from #1 recording every
  agent action.

**Expected benefits:** turns the product from "search engine with a parser"
into "an analyst that answers questions"; handles compound/unseen phrasings
without new patterns; dramatically expands addressable users (non-technical
investigators); creates the usage telemetry that feeds #5.

**Complexity:** Very High. Estimated effort: 6–10 sprints, phased (LLM
intent-classification first, full agents second).

**Architecture considerations:** Requires #3's clean API (the agent's tool
surface) and #2's fresh data (grounding). The trace/replay model also becomes a
dataset for #5. This is the highest-risk, highest-reward item — phase it so the
deterministic engine remains the safety net at every step.

## 5. Self-Improving Intelligence Flywheel (MLOps) — New Strategic Direction

**Current limitation:** The seeds exist — `feedback.py`, `calibration.py`,
`golden.py`, `self_improve.py`, alias auto-learning — but they run as local,
manual, and unmeasured loops. There is no evaluation harness gating changes, no
central ground-truth store, and no systematic path from user corrections to
system improvement. Today the system learns incidentally; it cannot be proven
to be learning.

**Why it matters:** In this domain, accuracy compounds trust, and trust is the
product. Every "no, wrong address" or alias approval is labeled ground truth
being thrown away. A platform that demonstrably gets better with usage — with
numbers to prove it — is a durable moat that competitors without feedback loops
cannot replicate.

**Proposed solution:**

- CI-gated evaluation harness: promote the golden set to a regression gate in
  CI (#3): no merge ships if recall/precision regress. Extend the golden set
  from the feedback loop automatically.
- Feedback → ground truth pipeline: every correction, alias approval, and
  clarification outcome is versioned and stored centrally (Postgres tables),
  then mined into: (a) rule/pattern tuning in `intents.json`, (b)
  alias/entity-resolution updates, (c) LLM prompt optimization and, later,
  fine-tuning datasets for the copilot (#4).
- Governed learned assets: aliases, rules, and calibration thresholds become
  versioned, diffable artifacts with review workflows (the existing Alias
  Review page becomes the model-governance UI).
- Drift & quality monitoring: scheduled quality scans (reusing the existing
  quality machinery) with alerts when freshness or match rates degrade.

**Expected benefits:** measurable, compounding accuracy; automated expansion of
coverage (new merchants, new phrasings) without developer intervention; a
defensible "our system is provably the most accurate" claim.

**Complexity:** High. Estimated effort: 4–6 sprints.

**Architecture considerations:** This item is only possible after #1
(audit/correction capture must be trusted), #2 (central ground-truth store), #3
(CI gate, telemetry), and #4 (LLM outputs to evaluate). It converts the app's
activity into a strategic asset — the final compounding payoff.

## Execution Roadmap: 1 → 2 → 3 → 4 → 5

The order is deliberate — each step de-risks and enables the next:

- **Security first.** The system holds PII; centralizing data (step 2) before
  securing access would be reckless. Security also creates the audit capture
  that the flywheel (step 5) depends on.
- **Data platform second.** You can't build an API or an agent on three
  manually-rebuilt SQLite files. Fresh, lineage-proven data is the substrate
  everything else queries.
- **API + observability third.** The clean, versioned, instrumented API is
  simultaneously the integration product and the tool surface the copilot
  (step 4) needs — and CI + tracing are the measurement apparatus the flywheel
  (step 5) requires.
- **Agentic copilot fourth.** It compounds everything before it: a secure,
  fresh, queryable data platform served through a clean API that an agent can
  reason over — and it produces the rich interaction traces step 5 turns into
  ground truth.
- **Flywheel last, but it never finishes.** Once telemetry, ground truth, and
  evaluation exist, the system enters permanent self-improvement — the state
  that makes steps 1–4 increasingly valuable over time.

**12–24 month vision:** a governed, multi-tenant merchant intelligence platform
where a compliance-audited API serves the whole NNPC/2ISW ecosystem, an
agentic copilot resolves investigations in minutes instead of hours, and a
measured feedback flywheel gives you accuracy no competitor can match because
theirs has no loop. The journey is long, but every step is independently
valuable and each one unlocks the next.

**One honest caveat:** this roadmap assumes the codebase is as well-factored as
the README suggests. Before committing, do a quick technical due-diligence pass
on `api.py` and the rebuild scripts — if the coupling is worse than documented,
budget one additional refactor sprint inside step 3.

---

## Appendix A: Due-diligence verification (August 2026)

Read-only verification of the review's code-level claims against the actual
repo (file `api.py`, `merchant_intelligence/`, `data/`, `tests/`):

| # | Review claim | Verdict | Evidence |
|---|---|---|---|
| 1 | No auth/authz/masking/audit | Confirmed | `api.py`: 0 hits for `auth`, `login`, `session`, `Authorization`, `Bearer`, `password`, `audit`, `mask`, `rbac`, `role`, `permission`, `secret`, `encrypt`, `Depends`, `get_current_user`. Only middleware is CORS. The only `api_key` is `LLM_API_KEY` for `/api/brief`. The 45-column `merchants` table really does store BVN / account numbers / static accounts. |
| 2 | 3 SQLite files, manual rebuild, denormalized, no migrations | Confirmed | 3 DBs on disk (270/212/186 MB). `intelligence.db` = one denormalized `merchants` table (45 cols, 76,875 rows) + FTS5/trigram shadow tables + aux tables. No migration tooling (`alembic.ini`/`migrations/` absent). |
| 3 | `api.py` monolith, no versioned API, no observability, Excel-first | Confirmed (numbers understated) | **44 routes** in one 1,675-line file (review said ~30). 0 `/api/vN` paths. 0 OpenTelemetry/tracing refs. No Dockerfile, docker-compose, or CI workflows. 16 openpyxl/xlsx references — Excel export is first-class. |
| 4 | Closed-world regex parser; LLM relegated to `/api/brief` | Confirmed | **27 intents** in `intents.json` (review said ~25). LLM = `urllib` OpenAI-compatible call in `brief.py` only; nothing else leaves the machine. |
| 5 | Self-improvement seeds exist but manual/unmeasured | Confirmed | `feedback.py` (500 ln), `calibration.py` (412), `golden.py` (213), `preferences.py`, `aliases.py`, `scripts/self_improve.py` all present. No CI gate, no central ground-truth store (`search_history` currently 0 rows). |
| — | "590+ tests" | Confirmed in substance | 593 checks in a **custom `check()` harness** (no pytest, 0 `assert` statements) in `tests/test_tasks.py` (2,480 lines); ~4,223 lines of tests total. |

**Two nuances to the review:**

1. **The engine is better-layered than the review assumes.** The intent engine
   already lives in a clean, independently-testable package
   (`merchant_intelligence/tasks/` — engine/intents/parser/pipelines/db/models/
   vocab), so several `api.py` route handlers are thin wrappers over that
   service layer. The refactor in step 3 is mostly extracting route + formatting
   glue, not untangling business logic.
2. **Partial lineage already exists.** `raw_data` JSON preserves the full source
   row, `sheet_name` encodes `file :: sheet`, and `imported_at`/`row_number`
   are captured. What's missing is an ingestion-run model and cross-row
   provenance — the upgrade is additive rather than greenfield.

**Bottom line:** the review is technically accurate — every code-level claim
holds, and where it estimated numbers (~30 routes, ~25 intents) the real values
are close (44, 27). The strategic thesis and the 1→2→3→4→5 ordering are
consistent with the actual codebase.
