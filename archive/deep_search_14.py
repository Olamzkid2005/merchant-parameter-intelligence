"""
Deep-search for 14 merchants across ALL columns and BOTH databases.
Shows: token matches, column matches, email/address/phone matches, FTS matches.
"""
import sqlite3
from pathlib import Path
from difflib import SequenceMatcher

# Both DBs
DB1 = r"C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db"
DB2 = r"C:\Users\David.Olamijulo\downloads\parameter\data\merchant_intel.db"

ALL_COLS = [
    "merchant_name", "slip_header", "email", "phone", "address",
    "contact_name", "account_name", "alias", "remarks",
]

def tokenise(name):
    import re
    if not name: return []
    name = name.upper().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return [w for w in name.split() if len(w) >= 3 and w not in {
        "THE","AND","FOR","LTD","LIMITED","NIGERIA","NG","SERVICES",
        "ENTERPRISES","COMPANY","LIMITED","GROUP","PLC","LTD",
    }]

MERCHANTS = [
    ("CRANE FIELD INTERNMATIONAL SCHOOL JEDDO", []),
    ("DENIKE AGORO ENTERPRISES", ["ADENIKE AGORO"]),
    ("FENCHURCH SERVICES LIMITED", []),
    ("G&G MULTISERVICES INVESTMENT LIMITED", []),
    ("LAGOON WATERS LTD", []),
    ("MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT", []),
    ("MONEYTRUST MICROFINANACE BANK LTD", []),
    ("MUSSAN OIL NIGERIA LIMITED", []),
    ("NEWHEALTH PHARMACY LTD 3", []),
    ("NWANERI VICTOR", []),
    ("OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED", []),
    ("PETER CHIDI ANUCHA", []),
    ("POWERFOIL GLOBAL SERVICES LIIMITED", []),
    ("ROSEFUN VENTURES", []),
]

for db_path in [DB1, DB2]:
    print(f"\n{'='*100}")
    print(f"  DATABASE: {Path(db_path).name}")
    print(f"{'='*100}")
    
    if not Path(db_path).exists():
        print(f"  (file not found)")
        continue
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Get the merchants table name (might differ between DBs)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name='merchants' OR name='merchants_fts')")
    tables = [r[0] for r in c.fetchall()]
    
    # Determine table and column schema
    merch_table = None
    for t in tables:
        if t != "merchants_fts":
            merch_table = t
            break
    if not merch_table:
        print("  (no merchants table)")
        conn.close()
        continue
    
    c.execute(f"PRAGMA table_info({merch_table})")
    db_cols = [r[1] for r in c.fetchall()]
    c.execute(f"SELECT COUNT(*) FROM {merch_table}")
    total = c.fetchone()[0]
    print(f"  Table: {merch_table}, {total} records")
    
    # Check which of our searchable columns exist
    avail_cols = [col for col in ALL_COLS if col in db_cols]
    print(f"  Available columns: {avail_cols}")
    
    for query, aliases in MERCHANTS:
        tokens = tokenise(query)
        all_search = list(set(tokens + [a.upper() for a in aliases]))
        
        print(f"\n  --- {query[:55]} ---")
        print(f"  Tokens: {all_search}")
        
        best_matches = []
        
        # 1. Search each token across ALL available columns
        for tok in all_search:
            if len(tok) < 3:
                continue
            for col in avail_cols:
                try:
                    c.execute(
                        f"SELECT * FROM {merch_table} WHERE {col} LIKE ? LIMIT 3",
                        (f"%{tok}%",)
                    )
                    for row in c.fetchall():
                        name = str(row.get("merchant_name", row.get("name", "")) or "")
                        if name:
                            best_matches.append({
                                "name": name,
                                "col": col,
                                "token": tok,
                                "val": str(row.get(col, ""))[:60],
                                "tid": row.get("tid") or row.get("terminal_id") or "",
                            })
                except:
                    pass
        
        # 2. Deduplicate by name, keep best per name
        seen = {}
        for m in best_matches:
            if m["name"] not in seen:
                seen[m["name"]] = m
        
        # 3. Score and sort
        scored = []
        for name, info in seen.items():
            name_upper = name.upper()
            matched_tokens = []
            for tok in all_search:
                if tok in name_upper:
                    matched_tokens.append(tok)
            score = len(matched_tokens) / max(len(all_search), 1) * 100
            # Bonus for matching in merchant_name column
            if info["col"] == "merchant_name":
                score += 10
            scored.append((score, info))
        
        scored.sort(key=lambda x: -x[0])
        
        # Show top 5
        for i, (score, info) in enumerate(scored[:5]):
            pct = min(score, 100)
            bar = "#" * (int(pct / 10)) + "." * (10 - int(pct / 10))
            print(f"  [{bar}] {pct:3.0f}%  {info['name'][:55]}")
            print(f"       Matched: {info['token']} in {info['col']} = \"{info['val'][:40]}\"")
            if info["tid"]:
                print(f"       TID: {info['tid']}")
        
        if not scored:
            print(f"  (zero matches across all columns)")
    
    conn.close()

print("\nDone!")
