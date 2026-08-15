# Merchant Parameter Intelligence

A merchant-search and investigation toolkit for the 2ISW / NNPC merchant
parameter files. Paste any fragment — a name, TID, MX code, phone, email,
account number, even a natural-language request like *"get the static account
for these TIDs"* — and the app finds every trace of that merchant across all
source workbooks and assembles a full profile.

**Stack:** FastAPI (Python) backend + React/Vite frontend + SQLite (FTS5)
databases. All data lives locally; nothing leaves the machine.

---

## Quick start

```bash
python app.start app --open     # start backend + frontend, open the browser
python app.start app            # start both, no browser
python app.start status         # what is running
python app.start stop           # stop everything
python app.start rebuild        # rebuild ALL databases from data/, then start
python app.start app --log-follow   # stream both services' logs to the console
```

- Backend API: **http://127.0.0.1:8000** (`/api/health` for health checks)
- Frontend: **http://127.0.0.1:5173** (Vite dev server, proxies `/api` → :8000)
- Double-click **`run.bat`** for the same thing on Windows.

Pre-flight checks run before launch: venv, deps, DB presence, node_modules,
frontend-utils sanity, and port availability. If a service fails to become
healthy in 60s the launcher reports which one and why.

---

## What it does (one paragraph)

Search any fragment → the **Search page** fuzzy-matches across
`merchant_name`, TID, MX code, phone, email, account, address and returns
scored rows with source file/sheet attribution. Click a row → the **Profile
page** aggregates every record that shares an identifier with that merchant
(emails, phones, TIDs, MX codes, addresses, name variants, which sheets each
trace appears in) plus a mini relationship network. The **Entity Graph**
visualises how records link through shared identifiers. Everything is also
reachable through the **intent parser**: the same search box understands
natural-language requests and executes them as multi-step pipelines (see
below).

---

## Architecture

```
parameter/
├── api.py                      # FastAPI backend — ALL HTTP endpoints live here
├── app.start                   # One-command launcher (backend + frontend + rebuild)
├── run.bat / stop.bat          # Windows double-click launcher / stopper
├── merchant_intelligence/      # Core engine package (do NOT reorganize)
│   ├── config.py               # Paths, weights, thresholds, data-dir resolution
│   ├── database.py             # SQLite FTS5 + trigram wrapper, name buckets
│   ├── matcher.py              # Fuzzy/phonetic/token scoring (MerchantMatcher)
│   ├── search.py               # High-level search API (MerchantSearch)
│   ├── aliases.py              # Alias engine + auto-learning
│   ├── entity.py               # Entity resolution (link records into families)
│   ├── profile.py              # MerchantProfile: aggregate + relationship network
│   ├── fuzzy.py                # rapidfuzz/jellyfish helpers + pure-Python fallbacks
│   ├── idclass.py              # Identifier classifier (TID vs MX vs phone vs …)
│   ├── enrich.py               # key extraction + timeline for profiles
│   ├── calibration.py          # Confidence calibration (ask/gap thresholds)
│   ├── preferences.py          # "Remember my choice" phrase→intent store
│   ├── feedback.py             # Request log + pattern mining (self-improvement)
│   ├── settings.py             # engine_settings.json knobs (Rule Engine page)
│   ├── golden.py               # Benchmark golden set for the engine
│   └── tasks/                  # NLU-style intent parser (see below)
│       ├── engine.py           # detect_task / execute_task / clarify / analyze
│       ├── intents.py          # weighted intent analysis + typo-fuzzy tier
│       ├── intents.json        # TUNED PATTERNS — non-developer editable
│       ├── parser.py           # identifier/name/address/clause extraction
│       ├── pipelines.py        # one function per intent (static account, tid…)
│       ├── db.py               # SQL helpers (resolve_any, static_accounts_for_mx…)
│       ├── models.py           # TaskDescriptor / PipelineResult dataclasses
│       └── vocab.py            # slang map, states, address/stop words, key merchants
├── web/                        # React frontend
│   ├── src/pages/              # one file per page (see Pages below)
│   ├── src/components/         # shared UI (autocomplete, badges, tables…)
│   ├── src/utils/              # frontend logic (bank codes, exports, intents…)
│   └── vite.config.js          # dev proxy: /api → http://127.0.0.1:8000
├── scripts/                    # Operational CLI tools (build, sync, quality…)
├── tests/                      # Test scripts (run from project root)
├── data/                       # SOURCE DATA + DATABASES (gitignored)
├── reports/                    # Generated Excel exports
├── logs/                       # Runtime logs + PID files (gitignored)
└── archive/                    # One-off probes + LEGACY Streamlit UI (unmaintained)
```

---

## The intent parser (`merchant_intelligence/tasks/`)

The heart of the app. `POST /api/task` receives free text and decides:
is it a plain search (`is_task: false` → caller runs `/api/search`) or a
multi-step task?

### Request → plan flow

1. **`parser.py`** extracts identifiers (TID, MX, phone, email, static
   account, payable, alias, BVN, MID via `idclass.py` + DB-rooted rules),
   name lists, name+ID pairs, addresses, and clauses
   ("get email for 2103O338 **and** phone for MX141692").
2. **`intents.py`** scores every intent with weighted regexes from
   `intents.json` plus an offline fuzzy tier (typo tolerance: "sttic
   account", "medpluz") and a slang map ("acct mgr" → "account manager").
   Each intent returns `{score, confidence, matched[]}`.
3. **`engine.py`** picks the primary intent, handles disambiguation
   (subsumption, e.g. `static_account` covers payable+alias), negation
   ("…but not the change history"), workflow planning, and builds a
   `TaskDescriptor`.
4. **`suggest_clarification()`** — when confidence is low or intents race
   ("account details" → profile vs static account vs change history), the
   API returns `needs_clarification: true` with options; the UI shows a
   picker and re-posts with the chosen intent. A **"remember my choice"**
   toggle saves the phrase → intent so it auto-runs next time.
5. **`execute_task()`** runs the matching pipeline in `pipelines.py` and
   returns a render-ready table `{columns, rows, not_found, summary}`.
6. **`calibration.py`** logs every accept/override decision and re-fits the
   ask/gap thresholds from real usage (Rule Engine page shows the stats).
7. **`feedback.py`** logs every request + outcome tag and mines repeated
   phrasings into "Suggested patterns" on the Rule Engine page (3-sample
   guard before anything is proposed).

### Supported intents (each maps to a pipeline)

`static_account` (beneficiary + payable + alias), `tid`, `mxcode`, `email`,
`phone`, `address`, `bank`, `account_name`, `account_number`, `contact`,
`onboarded` (onboarding date), `state`, `source`, `profile`, `change_details`
(old vs new account history), `segment` ("all addresses of all nnpc
stations"), `count`, `duplicates`, `summary`, `compare` (side-by-side),
`verify`, `related`, `formerly` (name variants), `coverage`, `top`,
`per_state`, plus compound requests (any combination, merged into one table).

### Key-merchant routing

Big chains (MEDPLUS, ADDIDE, SPAR/ARTEE, BEACONHEALTH, FILMHOUSE, RUBELS,
LAGOON WATERS, CASCADES, BOKKU MART, ORIENT AFRICA, SHOPRITE, KONGAPAY,
GENESIS FOODS…) get special treatment: a request like "medplus emails"
resolves the family canonically and routes to the right field pipeline, with
typo tolerance ("adide" → ADDIDE). The matched roots are exposed as a
`key_merchants` badge on results.

### Address requests

Pasting addresses ("get me the tids for <addresses>") routes to a
high-precision **address-column** matcher (tiered AND/OR landmark queries +
token-overlap scoring) — never fuzzy name search — so a road+city string
can't return unrelated stores. Results include a "Matched Address" column so
each TID is verifiable. An explicit field word ("tids", "email"…) never
triggers the clarification popup even when address text scores other intents.

---

## API reference (`api.py`)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/stats` | record counts per sheet, DB info |
| POST | `/api/search` | fragment search (name/TID/MX/phone/email/account) |
| POST | `/api/search/export` | search results → styled xlsx |
| GET | `/api/autocomplete?prefix=` | name suggestions as you type (fuzzy) |
| POST | `/api/suggest` | "similar names" suggestions |
| POST | `/api/similar` | related merchants (shared identifiers) |
| POST | `/api/profile` | full merchant profile + linked records |
| POST | `/api/timeline` | chronological record view |
| POST | `/api/compare` | side-by-side profile comparison |
| POST | `/api/entity` | relationship-network data (graph) |
| POST | `/api/task` | intent parser: analyze + execute a request |
| POST | `/api/task/analyze` | full intent breakdown for the Rule Engine |
| POST | `/api/task/export` | task result table → styled xlsx |
| POST | `/api/batch` | name-list batch search |
| POST | `/api/batch/export` | batch → xlsx |
| POST | `/api/quickmatch` | precision-first identifier resolution |
| POST | `/api/quickmatch/export` | quick match → xlsx |
| POST | `/api/reconcile` | merchant list → reconciliation report |
| POST | `/api/reconcile/export` | reconcile → xlsx |
| POST | `/api/report` / `/api/report/export` | report builder |
| GET | `/api/quality` / `/api/quality/export` | data-quality scan |
| GET | `/api/duplicates` | duplicate clusters |
| GET | `/api/aliases` | alias review queue |
| POST | `/api/aliases/approve` / `reject` | alias moderation |
| POST | `/api/learn` | learn an alias mapping |
| GET | `/api/intents` | intent config (patterns/keywords) for tuning UI |
| GET | `/api/settings` | engine knobs (decisive-match threshold etc.) |
| GET | `/api/calibration` / `reset` | decision stats + fitted thresholds |
| GET | `/api/preferences` / `/api/preferences/forget` | saved interpretations |
| GET | `/api/feedback/suggestions` + `apply` / `reject` | mined patterns |
| GET | `/api/idclass/debug` | identifier-classification diagnostics |
| GET | `/api/selfimprove` | regression harness state |
| POST | `/api/brief` | LLM investigation brief (offline fallback) |

Every `*_export` endpoint returns a styled `.xlsx` (autofilter, frozen header,
colour-coded status) with a descriptive filename derived from the query —
frontend logic lives in `web/src/utils/exportName.js`.

---

## Frontend pages (`web/src/pages/`)

| Page | File | What it does |
|---|---|---|
| Search | `SearchPage.jsx` | main search bar + autocomplete, results table with source chips + key-merchant badge + export |
| Batch Search | `BatchPage.jsx` | paste a list of names → one table |
| Quick Match | `QuickMatchPage.jsx` | precision-first identifier resolution (matched field shown) |
| Entity Graph | `EntityGraphPage.jsx` | visual graph of records linked by shared identifiers; node search, depth control |
| Merchant Profile | `ProfilePage.jsx` | everything on one merchant, compare mode, relationship network |
| Reconcile | `ReconcilePage.jsx` | merchant list vs registry reconciliation |
| Rule Engine | `RuleEnginePage.jsx` | test panel for the intent parser, edit intents.json patterns, calibration stats, suggested patterns, preferences |
| Report Builder | `ReportBuilderPage.jsx` | custom reports → Excel |
| Alias Review | `AliasReviewPage.jsx` | approve/reject auto-learned aliases |
| Data Quality | `QualityPage.jsx` | quality scan results → Excel |

Pages are switched via `?page=<key>` URL params (shareable/bookmarkable).

---

## Databases & the rebuild pipeline

Three SQLite databases, all generated from the Excel workbooks in `data/`:

| DB | Role |
|---|---|
| `data/intelligence.db` | **Active DB** — the app reads this (ALL Excel files ingested) |
| `data/merchant_search.db` | Main DB (2ISW + NNPC, 70K+ records) |
| `data/merchant_intel.db` | Legacy-format synced copy |

### Rebuild (`python app.start rebuild`)

Runs in order:
1. `scripts/rebuild_db.py` — rebuild `merchant_search.db` from the 2ISW workbook
2. `scripts/build_intelligence_db.py` — rebuild `intelligence.db` from ALL
   `.xlsx` files in `data/` (auto-detects headers per sheet, handles
   report-style headers, stacked export blocks, headerless columns like a
   state column, and BENEFICIARY NAME → account_name mapping; excludes
   derived exports like `medplus_tids.xlsx`)
3. `scripts/sync_intel_db.py` — re-sync `merchant_intel.db`
4. `scripts/self_improve.py` — gate on recall baseline + mine alias candidates

### Add new data

1. Drop the workbook into `data/`
2. `python app.start rebuild` (or run the four scripts above)

Note: `build_intelligence_db.py` has a `--watch` flag that auto-rebuilds when
an Excel file changes, and a `verify_search()` step proving new files loaded.

---

## Configuration

| File | What to tune |
|---|---|
| `merchant_intelligence/tasks/intents.json` | **Intent patterns/keywords/weights + fuzzy on/off per intent + slang map** — non-developer editable, hot-reloaded via `PUT /api/intents` from the Rule Engine page |
| `merchant_intelligence/config.py` | Paths, search weights, thresholds, alias lists |
| `data/engine_settings.json` | Runtime knobs (decisive-match threshold…) editable from the Rule Engine page |
| `data/manual_aliases.json` | Hand-authored alias mappings |
| `data/merchant_aliases.json` | Auto-learned alias cache (from alias review) |
| `data/known_compounds.json` | Compound merchant names |

Environment: `LLM_API_KEY` (+ optional `LLM_BASE_URL`, `LLM_MODEL`) enables the
LLM investigation brief; without it, `/api/brief` falls back to a
deterministic offline template. `INTENTS_FILE` overrides the intent config
path.

---

## Running tests

All test files run from the project root:

```bash
python tests/test_tasks.py            # the big one: parser, intents, tasks, live API (590+ checks)
python tests/test_engine_v2.py        # core matching engine
python tests/test_engine_upgrades.py  # fuzzy/typo/score upgrades
python tests/test_autocomplete.py     # autocomplete + name buckets
python tests/test_identifier_search.py# identifier (phone/MX/TID/email) search
python tests/test_feedback.py         # self-improvement loop
python tests/test_new_features.py     # API feature smoke tests
python tests/test_next_level.py       # LLM brief + self-improve harness
python tests/test_semantic_shadow.py  # Tier-2 semantic layer (offline, shadow mode)
python tests/test_intent_golden.py    # golden-set novelty contract (offline)
python tests/test_app_start.py        # launcher pre-flight
python tests/test_watch_mode.py       # --watch rebuild flag
python tests/test_foreground_mode.py  # --log-follow mode
python tests/test_open_flag.py        # --open browser flag
```

`tests/test_tasks.py` hits the **live API** for its `[5*]` sections, so the
app must be running (`python app.start app`) for those to pass.

---

## Agent notes (reading this codebase)

- **Everything user-facing funnels through `api.py`** — start there for any
  feature. It imports `merchant_intelligence` (engine) and wires requests to
  pages.
- **Intent behavior is data-driven**: add/tune intents in
  `merchant_intelligence/tasks/intents.json`, not by editing regexes in code
  (patterns there override code defaults; tests verify they match).
- **The search engine is layered**: `MerchantSearch` (search.py) → `MerchantMatcher`
  (matcher.py) → `DatabaseManager` (database.py). Search scoring, aliases,
  and thresholds live in `config.py`.
- **DB schema**: the `merchants` table has one row per source-file record
  (columns include `merchant_name`, `tid`, `mxcode`, `phone`, `email`,
  `address`, `state`, `bank`, `static_acc_no`, `account_name`, `payable`,
  `alias`, `beneficiary`, `merchant_id` (MID), `onboarded`, `sheet_name`,
  `row_number`, `imported_at`). `sheet_name` doubles as file attribution
  ("<file> :: <sheet>").
- **Source of truth is `data/` Excel files** — the DBs are build artifacts.
  When investigating "why is X wrong", check the workbook first, then the
  build script mapping, then the DB.
- **`data/`, `*.xlsx`, DBs, `.venv/`, `logs/`, `web/node_modules/` are
  gitignored** — they never enter version control.

---

## Notes

- All path settings auto-detect the project root, so the folder can be moved
  freely.
- Auto-learned alias mappings persist to `data/merchant_aliases.json`.
- Runtime logs and PID files live in `logs/` (auto-created).
- `archive/` contains one-off investigation scripts and the **legacy
  Streamlit UI** (`archive/app.py` + `ui_theme.py`) — kept for reference, not
  maintained; use the React app.
