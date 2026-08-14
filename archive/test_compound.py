"""
Standalone test: compound token expansion with direct SQLite queries.
Tests how POWERFOIL, MONEYTRUST, and BEACONHEALTH get expanded.
"""
import sqlite3
from pathlib import Path

# Known compound parts (from config.py)
KNOWN_PREFIXES = {
    "POWER", "MULTI", "MICRO", "FINANCE", "PHARMA", "HEALTH",
    "ENTERPRISE", "SERVICE", "INVEST", "RESOURCE", "PETROLEUM",
    "NIGERIA", "INTERNATIONAL", "BEACON", "MONEY",
    "MANAGEMENT", "CONSULTING", "TECHNOLOGY", "SOLUTION",
    "PROPERTY", "REALTY", "ESTATE", "CAPITAL", "HOLDING",
    "TECHNICAL", "SECURITY", "LOGISTICS", "TRANSPORT",
    "COMMUNICATION", "DEVELOPMENT", "CONSTRUCTION",
}

KNOWN_SUFFIXES = {
    "FOIL", "CARE", "TECH", "SOLUTIONS", "SERVICES", "GROUP",
    "HOUSE", "LINE", "WORKS", "POINT", "ZONE", "LINK",
    "MARKET", "PLACE", "WORLD", "SYSTEMS", "NET", "SOFT",
    "WAY", "CITY", "PARK", "DALE", "FIELD", "BRIDGE",
    "HEALTH", "CARE", "PHARMA",
}

MIN_TOKEN_LENGTH = 3
GENERIC_WORDS = [
    "LIMITED", "LTD", "NIGERIA", "NG", "GLOBAL", "SERVICES",
    "ENTERPRISES", "ENT", "INVESTMENT", "INVESTMENTS", "INV",
    "COMPANY", "CO", "CORPORATION", "CORP", "INC", "INCORPORATED",
    "GROUP", "HOLDINGS", "HOLDING", "PLC", "INTERNATIONAL", "INTL",
    "TECHNOLOGIES", "TECH", "SOLUTIONS", "SYSTEMS", "CONSULTING",
    "CONSULTANCY", "VENTURES", "PROPERTIES", "REALTY", "ESTATE",
    "PRODUCTS", "INDUSTRIES", "IND", "FZC", "LLC", "FZE", "DMCC",
    "NIG",
    "THE", "A", "AN", "AND", "OR", "FOR", "TO", "IN", "ON", "AT",
    "BY", "WITH", "OF", "IS", "IT", "AS", "BE", "THIS", "THAT",
]


def tokenise(text: str):
    import re
    if not text:
        return []
    text = text.upper().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    generic = set(w.upper() for w in GENERIC_WORDS)
    significant = [w for w in words if w not in generic and len(w) >= MIN_TOKEN_LENGTH]
    return significant if significant else [w for w in words if len(w) >= MIN_TOKEN_LENGTH]


def expand_compound(tokens, conn):
    """Same algorithm as MerchantMatcher._expand_compound_tokens."""
    extra = []
    for token in tokens:
        upper = token.upper()
        if len(upper) <= MIN_TOKEN_LENGTH + 3:
            continue
        best_prefix = None
        best_suffix = None
        best_score = -1
        for split_at in range(MIN_TOKEN_LENGTH, len(upper) - MIN_TOKEN_LENGTH + 1):
            prefix = upper[:split_at]
            suffix = upper[split_at:]
            if len(prefix) < MIN_TOKEN_LENGTH or len(suffix) < MIN_TOKEN_LENGTH:
                continue
            p_viable = prefix in KNOWN_PREFIXES or token_in_db(prefix, conn)
            s_viable = suffix in KNOWN_SUFFIXES or token_in_db(suffix, conn)
            if not p_viable or not s_viable:
                continue
            # Known words get high base score to outweigh accidental DB
            # substring matches (e.g. "LTH" from HEALTH via LIKE).
            p_score = 50 if prefix in KNOWN_PREFIXES else token_db_count(prefix, conn)
            s_score = 50 if suffix in KNOWN_SUFFIXES else token_db_count(suffix, conn)
            score = p_score + s_score
            # Bonus if both are known words (pairs like POWER+FOIL)
            if prefix in KNOWN_PREFIXES and suffix in KNOWN_SUFFIXES:
                score += 50
            if score > best_score:
                best_prefix = prefix
                best_suffix = suffix
                best_score = score
        if best_prefix and best_suffix:
            if best_prefix not in extra and best_prefix not in tokens:
                extra.append(best_prefix)
            if best_suffix not in extra and best_suffix not in tokens:
                extra.append(best_suffix)
    return extra


def token_in_db(token, conn):
    if not token or len(token) < MIN_TOKEN_LENGTH:
        return False
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM merchants WHERE merchant_name LIKE ?", (f"%{token}%",))
    return c.fetchone()[0] > 0


def token_db_count(token, conn):
    if not token or len(token) < MIN_TOKEN_LENGTH:
        return 0
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM merchants WHERE merchant_name LIKE ?", (f"%{token}%",))
    # Cap at 20 to prevent common substrings (like "LTH" with 298 matches)
    # from overwhelming known-word scores.
    return min(c.fetchone()[0], 20)


def search_by_token(token, conn, limit=10):
    c = conn.cursor()
    c.execute("SELECT merchant_name, tid, mxcode FROM merchants WHERE merchant_name LIKE ? LIMIT ?",
              (f"%{token}%", limit))
    return c.fetchall()


# MAIN
DB_PATH = Path(r"C:\Users\David.Olamijulo\downloads\parameter\data\merchant_search.db")
print(f"Database: {DB_PATH} (exists: {DB_PATH.exists()})")
print()

conn = sqlite3.connect(str(DB_PATH))
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM merchants")
total = c.fetchone()[0]
print(f"Total merchants in DB: {total}")
print()

queries = ["POWERFOIL", "MONEYTRUST", "BEACONHEALTH"]

for query in queries:
    print("=" * 75)
    print(f"  SEARCH: {query}")
    print("=" * 75)
    
    tokens = tokenise(query)
    print(f"  Tokens (after tokenise): {tokens}")
    
    expanded = expand_compound(tokens, conn)
    print(f"  Compound expansion: {expanded}")
    
    all_search_tokens = list(set(tokens + expanded))
    print(f"  All search tokens:   {all_search_tokens}")
    
    print()
    print(f"  -- Per-token DB matches --")
    for tok in all_search_tokens:
        count = token_db_count(tok, conn)
        rows = search_by_token(tok, conn, limit=6)
        print(f"  [{tok}] {count} merchant(s) in DB:")
        for name, tid, mx in rows:
            safe_name = name[:55] if name else ""
            safe_tid = (tid or "")[:12]
            safe_mx = mx or ""
            print(f"    -> {safe_name:55s} TID={safe_tid:12s} MX={safe_mx}")
        if count > 6:
            print(f"    (and {count - 6} more)")
        print()
    
    print()

conn.close()
print("Done!")
