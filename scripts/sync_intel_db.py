"""
sync_intel_db.py — Sync merchant_intel.db from merchant_search.db (includes NNPC data).

This copies ALL records from merchant_search.db (main + NNPC) into merchant_intel.db
so both databases are consistent and searchable through either system.

Part of the rebuild pipeline (`app.start rebuild` / `--rebuild`): runs AFTER
rebuild_db.py has rebuilt merchant_search.db, keeping merchant_intel.db in sync
with the same data. Exits 0 on success, 1 on failure so the launcher can detect
drift/failure.

Run standalone:  .venv\\Scripts\\python.exe scripts/sync_intel_db.py
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

SOURCE_DB = DATA_DIR / "merchant_search.db"
TARGET_DB = DATA_DIR / "merchant_intel.db"


def sync_merchant_intel_db() -> bool:
    """Rebuild merchant_intel.db from merchant_search.db.

    Returns True on success, False when the source DB is missing or the
    sync fails, so callers (app.start) can abort startup on failure.
    """
    if not SOURCE_DB.exists():
        print(f"  [X] source DB missing: {SOURCE_DB}")
        print(f"      Rebuild it first: {PROJECT_ROOT / 'scripts' / 'rebuild_db.py'}")
        return False

    print("=" * 70)
    print("  SYNCING merchant_intel.db FROM merchant_search.db")
    print("=" * 70)

    # Connect to source
    src = sqlite3.connect(str(SOURCE_DB))
    src.row_factory = sqlite3.Row
    sc = src.cursor()

    # Count total records
    sc.execute("SELECT COUNT(*) FROM merchants")
    total = sc.fetchone()[0]
    sc.execute("SELECT COUNT(*) FROM merchants WHERE sheet_name LIKE 'NNPC:%'")
    nnpc_count = sc.fetchone()[0]
    print(f"\n  Source DB (merchant_search.db):")
    print(f"    Total rows:     {total:,}")
    print(f"    NNPC rows:      {nnpc_count:,}")
    print(f"    Main rows:      {total - nnpc_count:,}")

    # Drop and rebuild target
    if TARGET_DB.exists():
        TARGET_DB.unlink()
        print(f"\n  Deleted old {TARGET_DB.name}")

    tgt = sqlite3.connect(str(TARGET_DB))
    tgt.execute("PRAGMA journal_mode=WAL")
    tc = tgt.cursor()

    # Create schema matching merchant_intel.py
    tc.executescript("""
        CREATE TABLE merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_name TEXT,
            row_num INTEGER,
            merchant_name TEXT,
            slip_header TEXT,
            email TEXT,
            mobile_phone TEXT,
            contact_name TEXT,
            contact_title TEXT,
            physical_addr TEXT,
            account_name TEXT,
            terminal_id TEXT,
            merchant_id TEXT,
            ptsp TEXT,
            state_code TEXT,
            bank_code TEXT,
            raw_json TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS merchants_fts USING fts5(
            merchant_name, slip_header, email, mobile_phone,
            contact_name, contact_title, physical_addr,
            account_name, terminal_id, merchant_id,
            tokenize='porter unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS merchants_fts_trigram USING fts5(
            merchant_name, slip_header, email, phone,
            contact_name, address, account_name, tid, merchant_id,
            tokenize='trigram'
        );
        CREATE TABLE IF NOT EXISTS aliases (
            canonical TEXT,
            alias TEXT,
            source TEXT DEFAULT 'auto',
            UNIQUE(canonical, alias)
        );
        CREATE TABLE IF NOT EXISTS email_index (
            merchant_name TEXT,
            email TEXT,
            sheet_name TEXT,
            row_num INTEGER
        );
        CREATE TABLE IF NOT EXISTS learning_log (
            discovered TEXT,
            canonical TEXT,
            confidence REAL,
            timestamp TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_merchant_name ON merchants(merchant_name);
        CREATE INDEX IF NOT EXISTS idx_alias ON aliases(alias);
    """)
    tgt.commit()

    # Copy data from merchant_search.db to merchant_intel.db
    print(f"\n  Copying records...")

    sc.execute("SELECT * FROM merchants ORDER BY id")
    counter = 0
    fts_failures = 0
    trigram_failures = 0
    email_failures = 0

    for row in sc.fetchall():
        row = dict(row)

        # Map merchant_search columns to merchant_intel columns
        rec = {
            "sheet_name": row.get("sheet_name", ""),
            "row_num": row.get("row_number", 0),
            "merchant_name": row.get("merchant_name", ""),
            "slip_header": row.get("slip_header", ""),
            "email": row.get("email", ""),
            "mobile_phone": row.get("phone", ""),
            "contact_name": row.get("contact_name", ""),
            "contact_title": row.get("contact_title", ""),
            "physical_addr": row.get("address", ""),
            "account_name": row.get("account_name", ""),
            "terminal_id": row.get("tid", ""),
            "merchant_id": row.get("merchant_id", ""),
            "ptsp": row.get("ptsp", ""),
            "state_code": row.get("state_code", ""),
            "bank_code": row.get("bank", ""),
            "raw_json": row.get("raw_data", "{}"),
        }

        tc.execute("""INSERT INTO merchants
            (sheet_name, row_num, merchant_name, slip_header, email,
             mobile_phone, contact_name, contact_title, physical_addr,
             account_name, terminal_id, merchant_id, ptsp, state_code,
             bank_code, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            rec["sheet_name"], rec["row_num"], rec["merchant_name"],
            rec["slip_header"], rec["email"], rec["mobile_phone"],
            rec["contact_name"], rec["contact_title"], rec["physical_addr"],
            rec["account_name"], rec["terminal_id"], rec["merchant_id"],
            rec["ptsp"], rec["state_code"], rec["bank_code"], rec["raw_json"],
        ))
        new_id = tc.lastrowid

        # FTS5 record (porter) — uses merchant_intel.db physical column names
        fts_cols = "merchant_name, slip_header, email, mobile_phone, contact_name, contact_title, physical_addr, account_name, terminal_id, merchant_id"
        fts_vals = (rec["merchant_name"], rec["slip_header"], rec["email"],
                    rec["mobile_phone"], rec["contact_name"], rec["contact_title"],
                    rec["physical_addr"], rec["account_name"], rec["terminal_id"],
                    rec["merchant_id"])

        try:
            tc.execute(f"INSERT INTO merchants_fts(rowid, {fts_cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (new_id,) + fts_vals)
        except Exception:
            fts_failures += 1
        # Trigram FTS (substring / typo-tolerant search) — uses the SAME canonical
        # column names migrate_trigram.py resolves (phone/address/tid), so running
        # sync and migrate in any order keeps both scripts working.
        trigram_cols = "merchant_name, slip_header, email, phone, contact_name, address, account_name, tid, merchant_id"
        trigram_vals = (rec["merchant_name"], rec["slip_header"], rec["email"],
                        rec["mobile_phone"], rec["contact_name"], rec["physical_addr"],
                        rec["account_name"], rec["terminal_id"], rec["merchant_id"])
        try:
            tc.execute(
                f"INSERT INTO merchants_fts_trigram(rowid, {trigram_cols}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new_id,) + trigram_vals
            )
        except Exception:
            trigram_failures += 1

        # Email index
        if rec["email"] and rec["email"] not in ("Y", "YES", ""):
            try:
                tc.execute("INSERT INTO email_index VALUES (?,?,?,?)",
                          (rec["merchant_name"], rec["email"], rec["sheet_name"], rec["row_num"]))
            except Exception:
                email_failures += 1

        counter += 1
        if counter % 5000 == 0:
            tgt.commit()
            print(f"    {counter:,} records copied...")

    tgt.commit()
    print(f"    {counter:,} records copied!")
    if fts_failures or trigram_failures or email_failures:
        print(f"  ⚠  Sync completed with index write failures:"
              f"  FTS={fts_failures}  trigram={trigram_failures}  email_index={email_failures}"
              f"  (search may be incomplete - check the schema)")

    # Generate aliases
    print(f"\n  Generating aliases for all merchant names...")
    tc.execute("SELECT DISTINCT merchant_name FROM merchants WHERE merchant_name != '' AND merchant_name IS NOT NULL")
    names = [r[0] for r in tc.fetchall()]

    GENERIC_WORDS = [
        "LTD", "LIMITED", "NIGERIA", "NIG", "GLOBAL", "SERVICES", "ENTERPRISES",
        "ENTERPRISE", "INVESTMENT", "INVESTMENTS", "PLC", "CORPORATION", "CORP",
        "GROUP", "HOLDINGS", "HOLDING", "SOLUTIONS", "TECHNOLOGIES", "TECHNOLOGY",
        "VENTURES", "CONCEPTS", "RESOURCES", "INTEGRATED", "NETWORK", "NETWORKS",
        "SYSTEMS", "INTERNATIONAL", "INTL", "ASSOCIATES", "PARTNERS", "TRADING",
        "INDUSTRIES", "COMPANY", "CO", "AND", "THE", "OF", "FOR", "AT", "BY", "ON",
        "NIGERIAN", "NIG LTD", "NIGERIA LTD",
    ]

    alias_count = 0
    for name in names:
        if not name:
            continue
        name_upper = name.upper().strip()
        aliases = set()
        aliases.add(name.strip())
        aliases.add(name_upper)

        tokens = name_upper.split()
        filtered = [t for t in tokens if t not in GENERIC_WORDS and len(t) > 1]
        if filtered:
            aliases.add(" ".join(filtered))
            for t in filtered:
                aliases.add(t)

        for alias in aliases:
            try:
                tc.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?)",
                          (name, alias, "auto"))
                alias_count += 1
            except Exception:
                pass

    tgt.commit()
    print(f"    {alias_count:,} aliases generated")

    # Stats
    tc.execute("SELECT COUNT(*) FROM merchants")
    final_count = tc.fetchone()[0]
    tc.execute("SELECT COUNT(*) FROM merchants WHERE sheet_name LIKE 'NNPC:%'")
    final_nnpc = tc.fetchone()[0]
    tc.execute("SELECT COUNT(DISTINCT sheet_name) FROM merchants")
    sheet_count = tc.fetchone()[0]

    src.close()
    tgt.close()

    print(f"\n{'=' * 60}")
    print(f"  SYNC COMPLETE!")
    print(f"  {'Total records:':20s} {final_count:,}")
    print(f"  {'NNPC records:':20s} {final_nnpc:,}")
    print(f"  {'Source sheets:':20s} {sheet_count}")
    print(f"  {'Target:':20s} {TARGET_DB.name}")
    print(f"{'=' * 60}")
    return True


def main() -> int:
    ok = sync_merchant_intel_db()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
