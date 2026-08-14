"""
Investigate suspicious 10.0 scores by tracing the full scoring pipeline.
"""
import sys, re
sys.path.insert(0, r"C:\Users\David.Olamijulo\downloads\parameter")
from difflib import SequenceMatcher
from merchant_intelligence import MerchantSearch, MerchantMatcher, DatabaseManager, config

MERCHANTS = [
    "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO",
    "DIVINE HARCO MEDICINES",
    "PETER CHIDI ANUCHA",
    "BIDGBENGA NIG LTD",
]

db = DatabaseManager()
searcher = MerchantSearch()
matcher = MerchantMatcher(db)

for query in MERCHANTS:
    print("=" * 80)
    print(f"INVESTIGATING: {query}")
    print("=" * 80)

    # 1. Token analysis
    tokens = MerchantMatcher._tokenise(query)
    print(f"\n  [TOKENS] {tokens}")

    expanded = matcher._expand_compound_tokens(tokens)
    all_tokens = list(set(tokens + expanded))
    print(f"  [EXPANDED] {expanded}")
    print(f"  [ALL TOKENS] {all_tokens}")

    # 2. FTS results
    fts_query = " ".join(all_tokens) if all_tokens else query
    fts_rows = db.search_fts(fts_query, limit=20)
    print(f"\n  [FTS] '{fts_query}' -> {len(fts_rows)} results")
    for row in fts_rows[:5]:
        name = row.get("merchant_name", "")[:60]
        rank = row.get("rank", "?")
        print(f"    rank={rank:.2f}  {name}")

    # 3. Column search results
    col_rows = []
    for tok in all_tokens[:5]:
        try:
            rows = db.search_by_column("merchant_name", tok, limit=10)
            col_rows.extend(rows)
        except ValueError:
            pass
    # Deduplicate
    seen_ids = set()
    deduped = []
    for r in col_rows:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            deduped.append(r)
    print(f"\n  [COLUMN SEARCH] deduped: {len(deduped)} merchants")
    for row in deduped[:8]:
        name = row.get("merchant_name", "")[:60]
        tid = row.get("tid", "")[:12]
        print(f"    {name:60s}  TID={tid}")

    # 4. Full search results with detailed scoring
    print(f"\n  [FULL SEARCH - TOP 8 RESULTS WITH FIELD SCORES]")
    results = matcher.search(query, limit=8, min_score=0)
    print(f"    Total results: {len(results)}")
    for i, res in enumerate(results[:8]):
        name = res.record.get("merchant_name", "")[:55]
        print(f"\n    #{i+1}: {res.overall_score:6.1f}/100  {res.match_type}")
        print(f"         Name: {name}")
        print(f"         Matched tokens: {res.matched_tokens}")
        if res.token_similarities:
            sims = ", ".join(f"{k}={v:.2f}" for k, v in res.token_similarities.items())
            print(f"         Token sims: {sims}")
        # Field breakdown
        if res.field_scores:
            fields = ", ".join(f"{k}={v:.1f}" for k, v in sorted(res.field_scores.items()) if v > 0)
            print(f"         Fields: {fields}")

    # 5. Check for any alias/slip_header/email matches
    print(f"\n  [ALL FIELDS CHECK - TOP MATCH]")
    if results:
        top = results[0]
        for key in ["merchant_name", "slip_header", "email", "phone", "address",
                     "account_name", "alias", "remarks", "tid", "mxcode"]:
            val = top.record.get(key, "")
            if val:
                print(f"    {key:20s} = {str(val)[:60]}")
    print()

db.close()
print("Done!")
