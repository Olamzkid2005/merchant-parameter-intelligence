"""
Use reference data (emails, exact names, clues) to find each merchant in the DB.
"""
import sys
sys.path.insert(0, r"C:\Users\David.Olamijulo\downloads\parameter")
from merchant_intelligence import MerchantSearch, DatabaseManager

db = DatabaseManager()
searcher = MerchantSearch()

# Reference data from user: (search_name, email, exact_name, notes)
MERCHANTS = [
    ("ADDIDE OGBA", "merchant1@example.com", "ADDIDE OGBA", ""),
    ("A-PURE LIFESTYLE PHARMACY NIGERIA LIMITED (A/C 2)", "merchant2@example.com", "", ""),
    ("Artee Industries Limited", "merchant3@example.com", "ARTEE INDUSTRIES LIMITED", ""),
    ("ATREOS RETAIL PLATFORM LIMITED-ACME (NGN)", "merchant5@example.com", "", ""),
    ("BEACONHEALTH DIAGNOSTICS", "merchant6@example.com", "", ""),
    ("BIDGBENGA NIG LTD", "merchant7@example.com", "", "biddel oil and gas is closest"),
    ("BOMART INTEGRATED SERVICES NIG LTD", "merchant8@example.com", "", ""),
    ("CRANE FIELD INTERNMATIONAL SCHOOL JEDDO", "", "", ""),
    ("DENIKE AGORO ENTERPRISES", "merchant10@example.com", "", "displayed as ADENIKE AGORO, related to medplus"),
    ("DIVINE HARCO MEDICINES", "merchant11@example.com", "DIVINE HARCO MEDICINES", ""),
    ("EBENEZER OJO OLADAPO", "merchant12@example.com", "", "closest match EBENEZER ONWAYADI 1"),
    ("E'SORAE HOME STORES LIMITED(IKOTA STORE)", "merchant13@example.com", "", ""),
    ("FENCHURCH SERVICES LIMITED", "", "", ""),
    ("FOLASHADE OLAJUMOKE KALEJAIYE", "merchant14@example.com", "", ""),
    ("G&G MULTISERVICES INVESTMENT LIMITED", "", "", ""),
    ("HARRISON OGOCHUKWU EZEASOMBA", "merchant17@example.com", "", ""),
    ("HEAVENLY DEWS GLOBAL CONCEPTS LIMITED", "merchant18@example.com", "", ""),
    ("KELIZZ INTEGRATED SERVICES LIMITED", "merchant19@example.com", "", ""),
    ("LAGOON WATERS LTD", "", "", ""),
    ("MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT", "", "", ""),
    ("MONEYTRUST MICROFINANACE BANK LTD", "", "", ""),
    ("MUSSAN OIL NIGERIA LIMITED", "", "", ""),
    ("NEWHEALTH PHARMACY LTD 3", "", "", ""),
    ("NWANERI VICTOR", "", "", ""),
    ("OLWADAMS PETROLEUM OIL AND GAS RESOURCES LIMITED", "", "", ""),
    ("PETER CHIDI ANUCHA", "", "", ""),
    ("PICCADILLY SUITES", "merchant26@example.com", "PICCADILLY SUITES", ""),
    ("POWERFOIL GLOBAL SERVICES LIIMITED", "", "", ""),
    ("REIZ CONTINENTAL HOTELS LIMITED", "merchant27@example.com", "REIZ CONTINENTAL HOTELS LIMITED", ""),
    ("ROSEFUN VENTURES", "", "", ""),
    ("RUBELS AND ANGELS RESTAURANT AJAO ESTATE BRANCH", "merchant28@example.com", "", ""),
    ("SEE BY JEF LIMITED", "merchant29@example.com", "SEE BY JEF LIMITED", ""),
    ("THE FILM HOUSE LIMITED", "merchant30@example.com", "", ""),
]

def search_by_email(email):
    """Search DB for a merchant by email - exact match only."""
    if not email:
        return []
    conn = db.connect()
    c = conn.cursor()
    # Split multi-email fields (separated by / or , or ;)
    emails = [e.strip() for e in email.replace(",", "/").replace(";", "/").split("/")]
    results = []
    for e in emails:
        if not e:
            continue
        # Try exact email match first
        c.execute("SELECT merchant_name, tid, mxcode, email, sheet_name, row_number FROM merchants WHERE email = ? LIMIT 3", (e,))
        rows = [dict(r) for r in c.fetchall()]
        if rows:
            results.extend(rows)
        else:
            # Try case-insensitive
            c.execute("SELECT merchant_name, tid, mxcode, email, sheet_name, row_number FROM merchants WHERE LOWER(email) = LOWER(?) LIMIT 3", (e,))
            rows = [dict(r) for r in c.fetchall()]
            if rows:
                results.extend(rows)
            else:
                # Try full email as substring
                c.execute("SELECT merchant_name, tid, mxcode, email, sheet_name, row_number FROM merchants WHERE email LIKE ? LIMIT 3", (f"%{e}%",))
                rows = [dict(r) for r in c.fetchall()]
                if rows:
                    results.extend(rows)
    return results

print("=" * 120)
print(f"{'QUERY NAME':55s} {'DB MATCH':55s} {'SCORE':6s} {'TID':15s} {'EMAIL':35s}")
print("=" * 120)

for search_name, email, exact_name, notes in MERCHANTS:
    best_name = ""
    best_score = 0
    best_tid = ""
    best_email = ""
    found_via = ""
    
    # Strategy 1: Search by email
    if email:
        email_rows = search_by_email(email)
        if email_rows:
            row = email_rows[0]
            best_name = row.get("merchant_name", "")
            best_tid = row.get("tid", "") or ""
            best_email = row.get("email", "") or ""
            best_score = 100
            found_via = "EMAIL"
    
    # Strategy 2: Search by exact name
    if not best_name and exact_name:
        r = searcher.search(exact_name, limit=5, min_score=0)
        if r and r[0].overall_score >= 50:
            best_name = r[0].record.get("merchant_name", "")
            best_tid = r[0].record.get("tid", "") or ""
            best_score = round(r[0].overall_score, 1)
            found_via = "EXACT"
    
    # Strategy 3: Search by query name
    if not best_name:
        r = searcher.search(search_name, limit=5, min_score=0)
        if r:
            best_name = r[0].record.get("merchant_name", "")
            best_tid = r[0].record.get("tid", "") or ""
            best_score = round(r[0].overall_score, 1)
            found_via = "SEARCH"
    
    # Strategy 4: Use notes clue
    if not best_name and notes:
        clue = notes.replace("displayed as ", "").replace("closest match ", "").split(",")[0]
        r = searcher.search(clue, limit=5, min_score=0)
        if r and r[0].overall_score >= 30:
            best_name = r[0].record.get("merchant_name", "")
            best_tid = r[0].record.get("tid", "") or ""
            best_score = round(r[0].overall_score, 1)
            found_via = "CLUE"
    
    # Strategy 5: Check ADENIKE for DENIKE
    if not best_name and "DENIKE" in search_name.upper():
        r = searcher.search("ADENIKE AGORO", limit=5, min_score=0)
        if r:
            best_name = r[0].record.get("merchant_name", "")
            best_tid = r[0].record.get("tid", "") or ""
            best_score = round(r[0].overall_score, 1)
            found_via = "ALIAS"
    
    # Status
    found = "FOUND" if best_name else "MISS"
    score_str = f"{best_score/10:.1f}" if best_name else "  - "
    print(f"{found} {search_name[:52]:52s} {best_name[:52] if best_name else '(not found)':52s} {score_str:6s} {best_tid:15s} {best_email[:35]:35s} [{found_via}]")

print()
print("=" * 120)

db.close()
