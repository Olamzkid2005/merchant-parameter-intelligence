# AI AGENT CONTEXT FILE — MERCHANT PARAMETER INTELLIGENCE

## Purpose

This file is a **project-memory and continuity handoff** for another AI coding
agent working in this repository. It captures the app's purpose, verified
architecture, engineering decisions (and *why* they were made), data quirks,
known issues, and the working agreements the owner expects. Read this before
changing anything; treat the **repository as the source of truth** — if this
file and the code disagree, the code wins.

The companion document `README.md` is a shorter agent-friendly guide; this file
adds the history, decisions, and gotchas that READMEs don't carry.

---

## 1. QUICK START (verified commands)

```bash
python app.start app --open       # backend + frontend, opens browser
python app.start app              # both, no browser
python app.start status           # what is running
python app.start stop             # stop everything
python app.start rebuild          # rebuild ALL DBs from data/, then start
python app.start app --log-follow # stream both services' logs
```

- Backend API: **http://127.0.0.1:8000** — health check `GET /api/health`
- Frontend: **http://127.0.0.1:5173** (Vite dev server, proxies `/api` → :8000)
- Windows double-click: **`run.bat`** (launch) / **`stop.bat`** (safe stop —
  only kills processes whose command line matches the venv python + app.start)
- `app.start` runs pre-flight checks (venv, deps, DBs, node_modules,
  frontend-utils sanity, ports) and fails with a clear message if a service
  isn't healthy within 60s.

Python lives in `.venv/Scripts/python.exe` (Windows venv). Node frontend is
plain Vite 5 + React — no TypeScript, plain JSX.

---

## 2. WHAT THE APP IS

A **merchant-search and investigation toolkit** for the 2ISW / NNPC merchant
parameter files (Interswitch Nigeria POS merchant registry data). Paste any
fragment — name, TID, MX code, phone, email, account number, address, even a
natural-language request ("get the static account for these TIDs") — and the
app finds every trace of that merchant across all source Excel workbooks and
assembles a full 360° profile.

The three core duties, as the owner frames them:

1. **Record of a merchant from any fragment** — search takes any identifier
   and the Profile page aggregates every row sharing an identifier: all
   emails, phones, TIDs, MX codes, addresses, name variants, and which
   file/sheet each trace appears in.
2. **Full merchant profile** — MRSP/MCC, settlement type, bank, account,
   contacts, addresses, slip headers, terminal owners, and the **onboarding
   date** (derived from the source files' "MONTH OF REQUEST" column).
3. **Tracing changes** — name variants across files (e.g. SPAR → ARTEE
   INDUSTRIES LIMITED via alias mappings) and the **Change of merchant
   details** sheet (old → new account history) surfaced as a timeline.

**Stack:** FastAPI (Python) backend + React/Vite frontend + SQLite (FTS5).
All data is local; nothing leaves the machine. `LLM_API_KEY` optionally
enables an LLM investigation brief; without it `/api/brief` uses a
deterministic offline template.

---

## 3. ARCHITECTURE MAP (verified)

```
parameter/
├── api.py                       # FastAPI backend — ALL HTTP endpoints (1675 lines; see "Outstanding work")
├── app.start                    # one-command launcher (start/stop/status/rebuild/watch)
├── run.bat / stop.bat           # Windows double-click launch / safe stop
├── merchant_intelligence/       # CORE ENGINE PACKAGE — do not reorganize
│   ├── __init__.py              #   public API (MerchantSearch etc.)
│   ├── config.py                #   paths, weights, thresholds, bank/state code maps, alias lists
│   ├── settings.py              #   engine_settings.json knobs (decisive-match threshold etc.)
│   ├── database.py              #   SQLite FTS5 + trigram wrapper, name buckets, SEARCHABLE_COLUMNS
│   ├── matcher.py               #   MerchantMatcher: fuzzy/phonetic/token scoring, identifier search
│   ├── search.py                #   MerchantSearch: high-level search API
│   ├── fuzzy.py                 #   rapidfuzz/jellyfish helpers + pure-Python fallbacks
│   ├── idclass.py               #   identifier classifier (TID vs MX vs phone vs account vs …)
│   ├── aliases.py               #   alias engine + auto-learning (merchant_aliases.json)
│   ├── entity.py                #   EntityResolver: link records into families via shared identifiers
│   ├── profile.py               #   MerchantProfile: 360° aggregation + relationship network + compare
│   ├── enrich.py                #   key extraction + terminal timeline (merchant_events)
│   ├── calibration.py           #   confidence calibration (ask/gap thresholds fitted from usage)
│   ├── preferences.py           #   "remember my choice" phrase → intent store
│   ├── feedback.py              #   request log + outcome tagging + pattern mining
│   ├── golden.py                #   benchmark golden set for the engine
│   ├── brief.py                 #   LLM investigation brief (offline fallback)
│   └── tasks/                   # NLU-STYLE INTENT PARSER (the heart of the app)
│       ├── engine.py            #   detect_task / execute_task / clarify / analyze / key-merchant tier
│       ├── intents.py           #   weighted intent scoring + typo-fuzzy tier + slang expansion
│       ├── intents.json         #   TUNED PATTERNS — non-developer editable, hot-reloaded
│       ├── parser.py            #   identifier / name / name+ID pair / address / clause extraction
│       ├── pipelines.py         #   one function per intent → render-ready {columns, rows, summary}
│       ├── db.py                #   SQL helpers (resolve_any, static_accounts_for_mx …)
│       ├── models.py            #   TaskDescriptor / PipelineResult dataclasses
│       └── vocab.py             #   slang map, states, address/stop words, key-merchant roots, defaults
├── web/                         # React frontend
│   ├── src/pages/               #   one file per page (see §7)
│   ├── src/components/          #   shared UI (autocomplete, CopyButton, badges, RelationshipNetwork …)
│   ├── src/utils/               #   frontend logic: exportName.js, bank.js, intents.js, matches.js …
│   └── vite.config.js           #   dev proxy: /api → http://127.0.0.1:8000
├── scripts/                     # OPERATIONAL CLI TOOLS (imported by app.start / api.py)
│   ├── rebuild_db.py            #   rebuild merchant_search.db from the 2ISW workbook
│   ├── build_intelligence_db.py #   rebuild intelligence.db from ALL .xlsx in data/ (main build)
│   ├── sync_intel_db.py         #   re-sync merchant_intel.db (legacy format)
│   ├── self_improve.py          #   recall-baseline gate + mine alias candidates
│   ├── reconcile.py, report.py, data_quality.py, batch_search.py, calibrate_weights.py,
│   ├── import_nnpc.py, migrate_trigram.py, check_deps.py, verify_nextlevel.py
├── tests/                       # test scripts, run from project root (see §9)
├── data/                        # SOURCE WORKBOOKS + DATABASES (gitignored) — see §4
├── reports/                     # generated Excel exports
├── logs/                        # runtime logs + PID files (gitignored)
└── archive/                     # retired one-off probes + LEGACY Streamlit UI (unmaintained)
```

---

## 4. DATA LAYER — SOURCE WORKBOOKS → DATABASES

### Source of truth: `data/*.xlsx`

The Excel workbooks ARE the source of truth; the databases are **build
artifacts**. When investigating "why is X wrong", check the workbook first,
then the build-script column mapping, then the DB.

Current workbooks (July 2026 batch): `2ISW_Parameter_File 5.xlsx` (14 sheets),
`NNpc parameter master.xlsx`, `NNPC PARAMETER FILE BATCH*.xlsx` (multiple),
`MRSP_Merchants.xlsx`, `Medplus.xlsx`, `static_account_terminal*.xlsx`,
`spar change of acc .xlsx`, `31 jul 2026.xlsx`, `Book2.xlsx`, plus derived
exports (`medplus_tids.xlsx`, `medplus_mids.xlsx`) that the build **excludes**.

### The three databases

| DB | Role |
|---|---|
| `data/intelligence.db` | **Active DB** the app reads — ALL workbooks ingested |
| `data/merchant_search.db` | Main DB (2ISW + NNPC, 70K+ records) — built by `rebuild_db.py` |
| `data/merchant_intel.db` | Legacy-format synced copy (`sync_intel_db.py`) |

### `merchants` table (45 columns, 76,875 rows currently)

`id, sheet_name, row_number, merchant_name, merchant_id, mxcode, payable_code,
tid, terminal_serial, slip_header, email, phone, address, contact_name,
contact_title, account_name, account_number, bank, state, state_code, bvn,
ptsp, terminal_type, deployment_status, alias, static_acc_no, remarks,
raw_data (JSON of the full source row), imported_at, onboarded_date,
merchant_category_code, business_occupation_code, terminal_owner_code,
settlement_type, acquirer, acquirer_id, lga, slip_footer, tin, mtn_serial,
sim9mobile_serial, deployment_date, bank_code, quality_score, quality_flags`

Key conventions:
- **`sheet_name` doubles as file attribution**: `"<file> :: <sheet>"` (e.g.
  `2ISW_Parameter_File 5 :: 2ISW_Parameter`). 27 distinct sheet values today.
- **`raw_data`** keeps the entire unmapped source row as JSON — nothing is
  ever truly lost, but unmapped columns aren't queryable.
- **`onboarded_date`** = the source "MONTH OF REQUEST" / "DATE CREATED"
  (normalised to first-of-month, e.g. `2021-10-01`). The profile shows the
  earliest date as "Onboarded".
- **`bank`** holds the resolved **bank NAME** (`First City Monument Bank
  (FCMB)`); the raw NIBSS code lives in **`bank_code`**. Same pattern for
  `state` (name) vs `state_code` (LA). Resolution happens at build time
  (`_resolve_code_names` in both build scripts).
- FTS5 index `merchants_fts` mirrors searchable fields — **it is a virtual
  table and does NOT support UPSERT** (see decisions §10).

### Rebuild pipeline (in order)

```bash
python app.start rebuild
```

1. `scripts/rebuild_db.py` — rebuild `merchant_search.db` from 2ISW
2. `scripts/build_intelligence_db.py` — rebuild `intelligence.db` from ALL
   `.xlsx` in `data/`; auto-detects headers per sheet, handles report-style
   headers, stacked export blocks, headerless columns (e.g. the state column),
   and BENEFICIARY NAME → account_name mapping; excludes derived exports;
   ends with `verify_search()` proofs (MRSP rows, Change sheet, ELEYELE SS,
   MAX-INFO column check) so a regression fails the build loudly. Has a
   `--watch` flag and writes live progress to `data/build_progress.txt`.
3. `scripts/sync_intel_db.py` — re-sync `merchant_intel.db`
4. `scripts/self_improve.py` — recall-baseline gate + mine alias candidates

To add new data: drop the workbook into `data/` — **the watch mode picks it up
automatically** (below). `python app.start rebuild` is the manual fallback.

### Incremental ingestion WATCH MODE (`merchant_intelligence/watcher.py`)

Shipped 2026-08-26 (roadmap #2). A daemon thread started by the API process
(`@app.on_event("startup")` in `api.py`, skipped under pytest, disabled with
`INGEST_WATCH=0`) that makes "drop a workbook in `data/`" the whole workflow:

1. **Poll** — every `INGEST_WATCH_INTERVAL`s (30) calls
   `ingest_ledger.freshness()`, which compares each Excel file's
   `(mtime_ns, size)` against the last good build's ledger snapshot (cheap —
   no hashing of ~100 MB workbooks).
2. **Debounce** — every stale file must be SETTLED (mtime ≥
   `INGEST_WATCH_SETTLE`s (20) old), so a workbook mid-save never enters a
   build.
3. **Cooldown** — no rebuild within `INGEST_WATCH_COOLDOWN`s (600) of the
   last one, so dropping several workbooks one-by-one doesn't storm.
4. **Rebuild** — closes the API's cached DB connections
   (`api_shared.reset_shared_singletons()` — required because the scripts
   DELETE the `.db` files, impossible on Windows while a connection is open;
   this is WHY the watcher lives inside the API process), then runs the same
   3-script pipeline as `app.start rebuild` as subprocesses
   (`sys.executable`, cwd = project root, output appended to
   `data/watch_rebuild.log`, 30-min timeout per script), then Nones the
   singletons so the next request lazily reconnects to the fresh DBs. Search
   is unavailable for the ~5–8 min rebuild; state is visible live.
5. **Harness non-fatal** — `self_improve.py` runs after the data scripts but
   its regression gate does NOT fail the rebuild (that would loop the
   watcher); it's reported as `harness_ok` in the status.

Endpoints: `GET /api/ingest/watch` (status + freshness),
`POST /api/ingest/watch/trigger` (queue an immediate rebuild). UI: watch
badge + "Scan & rebuild now" button on the Audit Trail page's ingestion
ledger card. Tests: `tests/test_watcher.py` (30 hermetic checks; the fake
ledger is patched on the `merchant_intelligence` PACKAGE attribute because
`_poll_once` does `from . import ingest_ledger`).

Hard-won gotchas (do not regress):
- **Ledger keys are case-normalized** (`os.path.normcase`):
  `build_intelligence_db.folder_snapshot()` snapshots `Path.resolve()`d paths
  (canonical casing, `.../Downloads/...`) while the freshness scan walks from
  `config.DATA_DIR` (inherits launch casing — run.bat vs a lowercase shell
  cd). Without normcase EVERY source reads as permanently "new".
- **`EXCLUDED_EXPORTS` must stay in sync** between the build script and
  `ingest_ledger.py`'s freshness scan: app-generated export workbooks
  (`medplus_tids.xlsx`, `medplus_mids.xlsx`) are deliberately never ingested;
  if the freshness scan doesn't skip them too, they read as "new" forever and
  the watcher rebuilds in an endless loop every cooldown.
- **Never force-kill the API mid-rebuild** — the build deletes the DB first,
  so an interrupted build leaves an empty `intelligence.db` (self-heals: the
  watcher sees stale/empty and rebuilds on its next cycle).

### Schema versioning + migrations (`merchant_intelligence/migrations.py`)

Shipped 2026-08-26 (roadmap #2). Version tracking = SQLite's native
`PRAGMA user_version` (in the DB file header — no extra table). An ordered,
append-only `MIGRATIONS` registry; `apply_migrations()` applies every version
> the DB's current one, stamps after each succeeds, never downgrades a newer
DB, and reports (never creates) a missing file.

- **v1** baseline marker; **v2** the data-platform tables (`source_files`,
  `identifiers`, `entity_clusters`, `data_quality_log` — mirrors schema.py's
  DDL for those four). Auth/encryption tables (app_users/roles, encryption_keys)
  deliberately stay behind the explicit `POST /api/schema/migrate` endpoint —
  auto-seeding a default admin on every boot would be a security smell.
- **Re-applied after EVERY rebuild** — rebuilds DELETE the DBs, so the
  pipeline (app.start step 5 AND the watcher's `REBUILD_STEPS`) ends with
  `python -m merchant_intelligence.migrations`; also best-effort at API
  startup. To add a schema change: append `(3, "...", DDL)` to `MIGRATIONS` —
  never edit an applied migration.
- `GET /api/ingest/watch` now returns `schema_versions` per DB (chip on the
  Audit Trail card). `POST /api/ingestion/scan` works again (its
  `source_files` table exists).
- Tests: `tests/test_migrations.py` (21 hermetic checks: upgrade, idempotency,
  partial upgrade, failure isolation, newer-DB guard, apply_all, shipped
  registry). In CI.

**Test-suite hygiene gotcha (cost us hours):** `tests/test_feedback.py`
used to apply patterns with `r'\\b...'` (DOUBLE backslash = literal
backslashes, not a word boundary) and without the `MERCHANT_INTENTS_VOCAB`
seam — so `apply_pattern`'s lockstep `regenerate_vocab_defaults()` wrote the
double-escaped pattern into the REAL vocab.py on every suite run. Both seams
(`MERCHANT_INTENTS_CONFIG` AND `MERCHANT_INTENTS_VOCAB`) are mandatory in any
test that calls apply_pattern/regenerate. Symptom if regressed:
`test_enrichment`'s "shipped config == shipped defaults" check fails and
static_account confidence drops.

---

## 5. THE INTENT PARSER (`merchant_intelligence/tasks/`)

The heart of the app. `POST /api/task` receives free text and decides: plain
search (`is_task: false` → caller runs `/api/search`) or a multi-step task.

### Request → plan flow

1. **`parser.py`** extracts identifiers (TID, MX, phone, email, static
   account, payable, alias, BVN, MID via `idclass.py` + DB-rooted rules),
   name lists, name+ID pairs, addresses, and clauses ("get email for
   2103O338 **and** phone for MX141692" attaches each intent to its own ID).
2. **`intents.py`** scores every intent: weighted regexes from `intents.json`
   + an offline fuzzy tier (typo tolerance: "sttic account", "medpluz") + a
   slang map (acct → account, mgr → manager). Confidence = `min(100, score*12)`.
3. **`engine.py`** picks the primary intent, handles disambiguation
   (subsumption: `static_account` covers payable+alias), negation ("…but not
   the change history"), workflow planning, and builds a `TaskDescriptor`
   (dataclass in `models.py`).
4. **Clarification** — when confidence is low or intents race ("account
   details" → profile vs static account vs change history), the API returns
   `needs_clarification: true` with options; the UI shows a picker and
   re-posts with the chosen intent. A "remember my choice" toggle saves the
   phrase → intent (`preferences.py`).
5. **`execute_task()`** runs the matching pipeline in `pipelines.py` and
   returns a render-ready table `{columns, rows, not_found, summary}`.
6. **`calibration.py`** logs accept/override decisions and re-fits the
   ask/gap thresholds from real usage (Rule Engine page shows stats).
7. **`feedback.py`** logs every request + outcome tag and mines repeated
   phrasings into "Suggested patterns" (3-sample guard before proposing).

### Supported intents (each maps to a pipeline in `pipelines.py`)

`static_account` (beneficiary + payable + alias), `tid`, `mxcode`, `email`,
`phone`, `address`, `bank`, `account_name`, `account_number`, `contact`,
`onboarded`, `state`, `source`, `profile`, `change_details` (old vs new
account), `segment` ("all addresses of all nnpc stations"), `count`,
`duplicates`, `summary`, `compare`, `verify`, `related`, `formerly`,
`coverage`, `top`, `per_state`, plus **compound** requests (any combination
merged into one table).

### How to add a brand-new intent (documented in intents.json `_help`)

1. `intents.json` — add an `"<intent>"` block with weighted patterns +
   keywords (copy an existing intent).
2. `pipelines.py` — write `_pipeline_<intent>(conn, task)` and register it in
   `_PIPELINES` (without this it silently falls back to generic resolve).
3. `vocab.py` — mirror patterns + keywords into `_DEFAULT_INTENT_PATTERNS` /
   `_DEFAULT_INTENT_KEYWORDS`. **The `[4h]` parity test asserts
   config == defaults, so a config-only intent fails tests.**
4. Optionally: `NAME_CAPABLE_INTENTS`, `CHAINABLE`, `SEGMENT_FIELDS`,
   `NAME_STOP_WORDS` in vocab.py.
5. Restart (`python app.start app`), run `python tests/test_tasks.py`, try it.

### Key-merchant routing

Big chains (MEDPLUS, ADDIDE, SPAR/ARTEE, BEACONHEALTH, FILMHOUSE, RUBELS,
LAGOON WATERS, CASCADES, BOKKU MART, ORIENT AFRICA, SHOPRITE, KONGAPAY,
GENESIS FOODS, MONEYTRUST …) get special treatment: a request like "medplus
emails" resolves the family canonically and routes to the right field
pipeline, with typo tolerance ("adide" → ADDIDE). Roots live in
`vocab.py` `_KEY_MERCHANT_ROOTS`; matched roots surface as a `key_merchants`
badge on results (Search page + Similar/Related + Batch rows).

### Address requests

Pasting addresses routes to a high-precision **address-column** matcher
(tiered AND/OR landmark queries + token-overlap scoring) — never fuzzy name
search — so a road+city string can't return unrelated stores. Results include
a "Matched Address" column. An explicit field word ("tids", "email"…) never
triggers the clarification popup even when address text scores other intents.

---

## 6. FRONTEND PAGES (`web/src/pages/`)

| Page | File | What it does |
|---|---|---|
| Search | `SearchPage.jsx` | main search bar + fuzzy autocomplete, results with source chips + key-merchant badge + export |
| Batch Search | `BatchPage.jsx` | paste a list of names → one table |
| Quick Match | `QuickMatchPage.jsx` | precision-first identifier resolution (shows matched field) |
| Copilot | `CopilotPage.jsx` | compound-request planner (roadmap #4): paste a multi-step investigation, see the decomposed plan + per-step results; LLM proposes the plan when a key is set, rule engine always executes/validates |
| Entity Graph | `EntityGraphPage.jsx` | visual graph of records linked by shared identifiers; node search + depth control + severity coloring |
| Merchant Profile | `ProfilePage.jsx` | 360° profile, Linked records table (with Bank/State/MCC/Settlement/LGA columns), relationship network, timeline, compare mode |
| Reconcile | `ReconcilePage.jsx` | merchant list vs registry reconciliation |
| Rule Engine | `RuleEnginePage.jsx` | intent-parser test panel, edit intents.json patterns live, calibration stats, suggested patterns, preferences |
| Report Builder | `ReportBuilderPage.jsx` | custom reports → Excel |
| Alias Review | `AliasReviewPage.jsx` | approve/reject auto-learned aliases |
| Data Quality | `QualityPage.jsx` | quality scan results → Excel |

Pages switch via `?page=<key>` URL params (shareable/bookmarkable).
All export buttons produce styled `.xlsx` (autofilter, frozen header,
colour-coded status) with **descriptive filenames** derived from the query —
logic in `web/src/utils/exportName.js` (locked in by a node sanity check run
during `app.start` preflight).

---

## 7. API REFERENCE (`api.py`)

`GET /api/health`, `GET /api/stats`, `POST /api/search` + `/api/search/export`,
`GET /api/autocomplete`, `POST /api/suggest`, `/api/similar`, `/api/profile`,
`/api/timeline`, `/api/compare`, `/api/entity`, `/api/task`,
`/api/task/analyze`, `/api/task/export`, `/api/batch` + export,
`/api/quickmatch` + export, `/api/reconcile` + export,
`/api/report` + `/api/report/export`, `GET /api/quality` + export,
`/api/duplicates`, `/api/aliases` + approve/reject, `/api/learn`,
`GET /api/intents` (+ `PUT` to save edited patterns), `GET /api/settings`,
`/api/calibration` + reset, `/api/preferences` + forget,
`/api/feedback/suggestions` + apply/reject, `GET /api/idclass/debug`,
`/api/selfimprove`, `POST /api/brief`, `POST /api/copilot` (roadmap #4:
compound-request decompose + execute, `{text, use_llm}` → plan + steps +
provenance; served on both `/api` and `/api/v1`), `GET /api/ingest`
(ledger runs/stats/freshness), `GET /api/ingest/watch` +
`POST /api/ingest/watch/trigger` (watch mode, roadmap #2),
`POST /api/ingestion/scan` (metadata-only CDC stub — does NOT rebuild;
the watcher is the real incremental path).

---

## 8. ENGINEERING DECISIONS & HISTORY (with rationale)

Git history: `464622f` initial import → `f22bf7d` README expansion →
`cebba65` scrubbed real merchant emails/phones from the public repo →
`3aa72c4` moved one-off diagnostics to `archive/` → `3c504c8` 13-column
capture + code-name resolution + placeholder repair + UI columns.

Key decisions and why:

1. **`tasks.py` → `tasks/` package** (engine/intents/parser/pipelines/db/
   models/vocab) with re-exports so imports stayed compatible. The intent
   parser outgrew one file; the split is the user's preferred separation of
   concerns, and pipelines are now independently testable.
2. **Intent patterns in `intents.json`, not code** — non-developers tune
   intents from the Rule Engine page without touching files; hot-reloaded via
   `PUT /api/intents`. Enforced by the `[4h]` config==defaults parity test.
3. **Decisive-match guard in `profile.py`** — a name search that wins at ≥
   `decisive_match_threshold` (tunable, ~9.0) only expands the family from
   records of the SAME merchant, so "OKI TINA" no longer drags in
   OKIEMUTE EKOKIFO's family. Identifier matches (phone/MX/TID/email) are
   exempt — they genuinely share the value. MEDPLUS's many entries stay
   together because they share name tokens/TIDs.
4. **Alias mappings** — SPAR → ARTEE INDUSTRIES LIMITED, MONEYTRUST
   MICROFINANCE (BANK) variants → CASCADES LUXURY LIMITED, etc. Live in
   `config.py` + `data/manual_aliases.json`; the alias engine auto-learns and
   the Alias Review page moderates.
5. **Report-style header handling** — `build_intelligence_db.py` detects
   report-style headers and stacked export blocks per sheet (e.g. 2ISW
   "Sheet1" has two export blocks with different layouts), so MRSP / static
   account terminal files load correctly (proven by `verify_search()`).
6. **Placeholder-name repair** — `NNpc parameter master` stored dealer names
   as `Interswitch Limited/NNPC N` because that placeholder *passed*
   `_is_real_name()` (it has letters), so the real dealer name in
   `CONTACTNAME` never won. New `_repair_placeholder_names()` post-process
   recovers the real name (263 rows; ELEYELE SS → MX184404 now resolves at
   9.9). **NIBSS/2ISW `INTERSWITCH LIMITED N` rows are legitimate BNPL
   collection accounts — deliberately left untouched.**
7. **FTS5 UPSERT bug** — FTS5 virtual tables reject `ON CONFLICT(rowid) DO
   UPDATE`; repair functions must DELETE then INSERT. Fixed in both build
   scripts (`_repair_code_names` had the same bug and double-executed).
8. **13 new columns + code-name resolution** — the 2ISW workbook had ~40
   columns but only ~20 were mapped; MCC, occupation, terminal owner code,
   settlement, acquirer, LGA, TIN, serials, bank_code etc. were lost to
   `raw_data`. Now captured in schema + both FTS indexes, and
   `_resolve_code_names()` converts bank codes → names and state codes →
   names at build time so the UI never shows bare codes.
9. **Email guard in both build scripts** — a repair that would wipe real
   emails is blocked; merchant_search.db and intelligence.db are rebuilt
   together (app.start rebuild) so they never drift.
10. **Safe stop.bat** — process-name gate so it can never kill an unrelated
    program on ports 8000/5173.

---

## 9. TESTING

Run from the project root:

```bash
python tests/test_tasks.py            # the big one: parser, intents, tasks, LIVE API (590+ checks)
python tests/test_engine_v2.py        # core matching engine
python tests/test_engine_upgrades.py  # fuzzy/typo/score upgrades
python tests/test_autocomplete.py     # autocomplete + name buckets
python tests/test_identifier_search.py# identifier (phone/MX/TID/email) search
python tests/test_feedback.py         # self-improvement loop
python tests/test_new_features.py     # API feature smoke tests
python tests/test_next_level.py       # LLM brief + self-improve harness
python tests/test_semantic_shadow.py  # Tier-2 semantic layer (offline, shadow mode; hermetic ENGINE_SETTINGS_FILE)
python tests/test_intent_golden.py    # golden-set novelty contract (offline)
python tests/test_enrichment.py       # Tier-1 WordNet enrichment pipeline (hermetic: fake synsets + temp config)
python tests/test_shadow_review.py    # Tier-2 §7 spot-check tooling + Phase-3 fit_tier2 gates + shadow_health (hermetic: temp shadow + review files; 47 checks)
python tests/test_audit.py            # append-only audit trail (roadmap #1 slice; hermetic: temp MERCHANT_AUDIT_DB; 18 checks)
python tests/test_auth.py             # opt-in authN/Z + RBAC + field masking (roadmap #1 slice; hermetic: temp config + sessions; 27 checks)
python tests/test_ingest_ledger.py    # ingestion-run ledger + freshness signal (roadmap #2 slice; hermetic: temp INGEST_LEDGER_FILE + temp source folder; 25 checks)
python tests/test_migrations.py       # schema versioning + ordered migrations via PRAGMA user_version (roadmap #2; hermetic temp DBs; 21 checks)
python tests/test_watcher.py          # incremental ingestion watch mode (roadmap #2; hermetic: fake ledger patched on the package attr, fake scripts, no subprocesses; 30 checks)
python tests/test_api_split.py        # api.py router-split parity + /api/v1 mirror (roadmap #3 slices; hermetic: 55-path baseline vs the last pre-split commit + deliberate-additions allowlist + legacy re-exports; 19 checks)
python tests/test_copilot.py          # Merchant Copilot (roadmap #4 slice; hermetic decompose + LIVE /api/copilot execution incl. the chained "find MEDPLUS then the static account for those" case; 38 checks)
python tests/test_app_start.py        # launcher pre-flight
python tests/test_watch_mode.py       # --watch rebuild flag
python tests/test_foreground_mode.py  # --log-follow mode
python tests/test_open_flag.py        # --open browser flag
```

`tests/test_tasks.py` hits the **live API** for its `[5*]` sections — the app
must be running (`python app.start app`) or those sections fail with
connection-refused (not a regression). Last full run: **all suites green**
(602 task checks incl. live API, 38 enrichment, 28 semantic shadow, 9 golden,
28 shadow review, 18 audit, 27 auth, 25 ingest ledger, 17 autocomplete,
19 api split, 38 copilot, 69 next-level) with the app up.

Also: `scripts/build_intelligence_db.py` ends with `verify_search()` proofs
(MRSP rows loaded, Change sheet rows loaded, ELEYELE SS resolves, MAX-INFO
column check) — a rebuild that regresses any of these fails loudly.

---

## 10. DATA QUIRKS & KNOWN ISSUES (verified against the live DB)

- **`INTERSWITCH LIMITED N` rows in NIBSS/2ISW sheets are real** (BNPL
  collection accounts where Interswitch IS the merchant) — do NOT "repair"
  them. In `NNpc parameter master`, leftover placeholders with no recoverable
  name also stay as-is.
- **Some merchants legitimately have multiple MX codes** (locations under
  the same person): LAGOON WATERS → MX183544 + MX183549, GAJI TAIWO →
  MX183567 + MX183570 ("GAJI TAIWO 2 - NNPC"), Ajibike Seun → MX184754 +
  MX186171. Do not treat as conflicts.
- **EMMANUEL ROTIMI has no MX anywhere in the DB**; MX183548 belongs to
  MARIA LAMBO. A pasted list claimed ROTIMI→MX183548 — wrong. There is no
  EMMANUEL ROTIMI record in the current build (only unrelated
  `ISW_ROTIMI …` names on MX17010).
- **`bank` shows the resolved name** unless the code is unknown (e.g. 057 not
  in the NIBSS map → name stays empty, code stays in `bank_code`).
- **MRSP_Merchants rows can have an empty `merchant_name`** (report-style
  file with the name in a different column or genuinely absent).
- **`onboarded_date`** is derived from "MONTH OF REQUEST" (first-of-month);
  it is NOT when the row was imported (`imported_at` is).
- **Derived exports** (`medplus_tids.xlsx`, `medplus_mids.xlsx`) are excluded
  from ingestion — only source workbooks build the DB.
- **`data/`, `*.xlsx`, DBs, `.venv/`, `logs/`, `web/node_modules/` are
  gitignored** — source data and DBs never enter version control. Never
  commit real merchant emails/phones (commit `cebba65` scrubbed them).
- **CLI tools installed per-user via Scoop** (`~/scoop/shims`, i.e.
  `C:\Users\<user>\scoop\shims`): `grep` (GNU 3.11), `rg`/ripgrep (15.2.0),
  `sed` (GNU 4.9), `awk`/`gawk` (GNU Awk 5.4.1), and GNU `coreutils`
  (`tail`, `head`, `wc`, `ls`, `cat`, `sort`, `uniq`, `sleep`, `cut`, …).
  Scoop added the shims dir to the Windows user PATH, **but a freshly
  spawned shell may not inherit the updated PATH** — if a tool reports
  `command not found`, prefix it with
  `export PATH="$HOME/scoop/shims:$PATH"`. (Python one-liners remain the
  reliable fallback, and the `code_search` tool's vendored ripgrep is still
  missing on this machine.)
- `logs/` holds runtime logs + PID files; `data/build_progress.txt` shows
  live rebuild progress.

---

## 11. WORKING AGREEMENTS (how the owner expects work to be done)

- **Inspect before changing.** Map the architecture, identify coupling and
  duplication, then propose. Reference actual file paths and symbols — never
  invent files, endpoints, DB tables, or implementations.
- **Separate concerns.** No HTTP handling + business logic + DB access + AI
  processing + formatting in one function. Routes should stay thin.
- **Root improvements in the DB.** The user's standing instruction: any new
  matching/intent/search behavior must be grounded in what the registry
  actually stores — verify against `data/intelligence.db` before coding.
- **Incremental, backward-compatible migrations.** Preserve working
  functionality; prefer small composable changes that make the next change
  easier.
- **Tests for important behavior.** Intent/parser changes must keep
  `tests/test_tasks.py` green (including the config==defaults parity check).
- **Technical communication.** Explain WHY, HOW, WHICH files, migration risk,
  DB/API implications. Avoid generic advice ("add comments").
- **No Codebuff-style commit footers** — plain descriptive commit messages.
- Current repo state (August 2026): branch `master`, remote
  `https://github.com/Olamzkid2005/merchant-parameter-intelligence` (public).

---

## 12. OUTSTANDING WORK (candidates, not committed plans)

- **Incremental ingestion watch mode is DONE** (roadmap #2) —
  `merchant_intelligence/watcher.py` auto-rebuilds all three DBs when Excel
  sources drift; see §4 for the architecture and its gotchas. Remaining from
  #2: schema versioning + migrations, source lineage.
- **`api.py` router split is DONE** (roadmap #3 slices 1–2) — handlers live in
  `api_routes/` (`auth_routes`, `profile_routes`, `search_routes`,
  `tasks_routes`, `admin_routes`) over `api_shared.py` (helpers, models,
  singletons, workbook styling); `api.py` is a slim bootstrap that mounts the
  routers and re-exports every handler/model so `import api; api.search(...)`
  keeps working. The routers are mounted TWICE over the same handlers:
  `/api` (legacy, byte-identical) and `/api/v1` (the stable versioned
  contract — 55 paths mirrored 1:1). Note: FastAPI 0.141 does no slash
  normalization, so router paths MUST be absolute (`/search`, not `search`).
  Path sets verified byte-identical on both mounts; `tests/test_api_split.py`
  locks parity. Next from the roadmap: OpenAPI-as-contract + JSON-first
  response envelope (Excel as explicit export transform), OpenTelemetry
  tracing/metrics, Docker packaging.
- **Copilot is DONE (first slice)** (roadmap #4) — `merchant_intelligence/
  copilot.py` + `POST /api/copilot` + Copilot page. Hybrid NLU: LLM proposes
  the step decomposition when `LLM_API_KEY` is set; otherwise the rule engine
  decomposes (whole-task → clause split). Every step executes through the
  deterministic engine (`detect_task`/`execute_task`/search) — the LLM can
  never inject identifiers or bypass a pipeline. Chaining: "find MEDPLUS then
  the static account for those" resolves "those/the above" against the
  previous step via `remember_entities` + `inherit_reference` + pronoun
  normalization (`_normalize_reference` maps those/them/these → "the above
  merchant" when the step has no entity of its own). Response is the
  replayable trace (plan + per-step results + mode/model/elapsed), audit-
  logged, on `/api` and `/api/v1`. Remaining copilot slices: RAG grounding,
  LLM tool-use over compare/reconcile/brief, trace→dataset feed into #5.
- **Frontend polish candidates** — search-results cards don't show the new
  fields (Bank/State/MCC); Linked records table could gain Terminal Owners /
  Acquirer columns.
- **Observability** — request logging exists; metrics + per-request tracing
  and a structured-log pass over `api.py` are not done.
- **API-layer tests** — most coverage is engine/task-level; the HTTP surface
  itself is only smoke-tested.

---

## 13. FINAL INSTRUCTION TO THE NEXT AGENT

Read this file as historical engineering context. Before acting: inspect the
repo, map the architecture, check which of the above still holds, and where
this file and the code disagree — **the code wins**. Build changes
incrementally, test every significant change (`tests/test_tasks.py` + the
rebuild `verify_search()` proofs), keep the system coherent, and preserve the
working agreements in §11.
