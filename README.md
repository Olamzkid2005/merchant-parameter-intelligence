# Merchant Parameter Intelligence

Merchant search engine + analysis toolkit for the 2ISW / NNPC merchant parameter files.

## Folder structure

```
parameter/
├── merchant_intelligence/   # Core search engine package (do not reorganize)
│   ├── config.py            # Paths, weights, thresholds, aliases (edit here)
│   ├── database.py          # SQLite FTS5 + trigram database wrapper
│   ├── matcher.py           # Fuzzy + phonetic + token matching & scoring
│   ├── search.py            # High-level search API (MerchantSearch)
│   ├── aliases.py           # Alias engine + auto-learning (Phase 10)
│   ├── entity.py            # Entity resolution (link records into families)
│   ├── profile.py           # Merchant profiles + relationship networks
│   └── fuzzy.py             # rapidfuzz / jellyfish helpers
│
├── web/                     # React frontend (Vite dev server)
│
├── data/                    # ALL source data + databases (read-only inputs)
│   ├── 2ISW_Parameter_File 5.xlsx
│   ├── NNPC PARAMETER FILE BATCH *.xlsx / NNpc parameter master.xlsx
│   ├── intelligence.db      # Active DB loaded by the app (ALL Excel files)
│   ├── merchant_search.db   # Main DB (main + NNPC data, 70K+ records)
│   ├── merchant_intel.db    # Legacy-format DB (synced copy)
│   ├── manual_aliases.json  # Editable alias mappings
│   ├── known_compounds.json # Editable compound-word lists
│   └── merchant_aliases.json  # Auto-learned alias cache
│
├── reports/                 # Generated Excel reports / exports
├── logs/                    # Runtime logs + PID files (auto-created)
├── tests/                   # Test scripts (run from the project root)
├── scripts/                 # Operational CLI tools (run with scripts/ prefix)
│   ├── batch_search.py      # Batch-search a merchant list
│   ├── reconcile.py         # Merchant list → Excel reconciliation report
│   ├── data_quality.py      # Quality scan of the database
│   ├── rebuild_db.py        # Full rebuild of merchant_search.db from Excel
│   ├── build_intelligence_db.py  # Rebuild intelligence.db from ALL Excel files
│   ├── import_nnpc.py       # Import NNPC Excel files into the DB
│   ├── migrate_trigram.py   # Add trigram FTS5 index to both DBs
│   ├── sync_intel_db.py     # Sync merchant_intel.db from merchant_search.db
│   ├── verify_nextlevel.py  # End-to-end verification of the engine
│   ├── self_improve.py      # Alias-free regression harness (feature #10)
│   └── check_deps.py        # Check required Python packages
│
├── archive/                 # One-off investigation scripts, diagnostic tools,
│                           #  legacy launchers, AND the legacy Streamlit UI
│                           #  (app.py + ui_theme.py — kept for reference; not maintained)
│
└── Root (app essentials only):
    ├── app.start            # One-command launcher (backend + frontend)
    ├── run.bat              # Double-click to start the app
    └── api.py               # FastAPI backend (serves web/)
```

## Far-out features

**#6 — LLM Investigation Brief.** The Profile page can generate a
natural-language investigation dossier for any merchant. Uses an LLM when
configured (set `LLM_API_KEY`, optionally `LLM_BASE_URL` / `LLM_MODEL` to
point at any OpenAI-compatible endpoint), otherwise falls back to a
deterministic offline template. Endpoint: `POST /api/brief`.

**#10 — Self-improving harness.** `scripts/self_improve.py` measures the RAW
engine strength with aliases DISABLED (so hand-added mappings can't hide
regressions), gates rebuilds on recall@1 against a stored baseline, and
auto-suggests alias candidates (from entity families) into the pending
review queue. Run it manually, or it runs automatically as step 4 of
`app.start rebuild`. Endpoint: `GET /api/selfimprove`.

## Quick start

```bash
python app.start app --open     # start backend + frontend (opens the browser)
python app.start app --rebuild  # rebuild ALL databases, then start
python app.start status         # what is running
python app.start stop           # stop everything
```

Starts the FastAPI backend (:8000) and the React/Vite frontend (:5173) with
pre-flight health checks. Individual tools:```bash
python scripts/check_deps.py                      # verify packages (rapidfuzz, jellyfish, pandas, openpyxl)
python scripts/batch_search.py                    # search all 33 merchants
python scripts/reconcile.py "THE FILM HOUSE LIMITED" -o reports/out.xlsx
python scripts/data_quality.py -o reports/q.xlsx
```

### Web UI (React, recommended)

Double-click `run.bat` (or `python app.start app --open`) to launch the
**React app** — frontend in `web/`, backend `api.py`. A **legacy Streamlit
UI** (`archive/app.py` with `archive/ui_theme.py`, run via
`archive/start_app.bat` / `python archive/start_app.py` on port 8501) is kept
for reference. The global Python environment had a corrupted Streamlit
install, so the project uses a dedicated virtual environment (`.venv/`).

## How to add data

1. Drop new Excel workbooks into `data/`.
2. `python app.start rebuild` — rebuild ALL databases from the Excel files
   (equivalent to running `scripts/rebuild_db.py` →
   `scripts/build_intelligence_db.py` → `scripts/sync_intel_db.py` →
   `scripts/self_improve.py` in order).

## Running tests

```bash
python tests/test_engine_upgrades.py     # core engine tests
python tests/test_identifier_search.py   # identifier (phone/MX/TID/email) search
python tests/test_new_features.py        # API feature smoke tests
python tests/test_next_level.py          # LLM brief + self-improve harness
```

## Notes

- All path settings live in `merchant_intelligence/config.py` (project root is
  auto-detected, so the folder can be moved freely).
- Auto-learned alias mappings persist to `data/merchant_aliases.json`.
- Runtime logs and PID files live in `logs/` (auto-created).
