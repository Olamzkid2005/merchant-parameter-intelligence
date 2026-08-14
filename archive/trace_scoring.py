"""
Directly trace _score_field to find the scoring bug.
"""
import sys
sys.path.insert(0, r"C:\Users\David.Olamijulo\downloads\parameter")
from difflib import SequenceMatcher
from merchant_intelligence import MerchantMatcher, DatabaseManager, config

db = DatabaseManager()
matcher = MerchantMatcher(db)

# Get a real row from column search for "SCHOOL"
rows = db.search_by_column("merchant_name", "SCHOOL", limit=3)
row = rows[0]

query = "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO"
tokens = MerchantMatcher._tokenise(query)
print(f"Query: {query}")
print(f"Tokens: {tokens}")
print()

# Manually call _score_row
from merchant_intelligence.matcher import SearchResult

def trace_score_row(row, tokens, raw_query):
    """Copy of matcher._score_row with debug prints."""
    record = dict(row)
    result = SearchResult(record.get("id"), record)

    merchant_name = str(record.get("merchant_name", "") or "")
    merchant_tokens = MerchantMatcher._tokenise(merchant_name)
    
    print(f"Merchant: {merchant_name[:60]}")
    print(f"Merchant tokens: {merchant_tokens}")

    matched_tokens = []
    token_similarities = {}
    for qtoken in tokens:
        best_sim = matcher._best_token_similarity(qtoken, merchant_tokens)
        token_similarities[qtoken] = best_sim
        if best_sim >= 0.60:
            matched_tokens.append(qtoken)
        print(f"  {qtoken:20s} best_sim={best_sim:.4f}  matched={'YES' if best_sim >= 0.60 else 'NO'}")

    print(f"  Matched tokens: {matched_tokens}")
    print()
    
    # For each field in FIELD_WEIGHTS, trace the score
    for field in config.FIELD_WEIGHTS:
        field_value = record.get(field)
        if not field_value:
            print(f"  {field:20s} = (empty)  -> score=0")
            continue
        fv_str = str(field_value)
        score = matcher._score_field(fv_str, tokens, raw_query, token_similarities)
        print(f"  {field:20s} = {str(fv_str)[:60]:60s}  score={score:.1f}")

print("=" * 80)
for merchant_token in ["SCHOOL", "FIELD", "CRANE", "PET"]:
    rows = db.search_by_column("merchant_name", merchant_token, limit=1)
    if rows:
        trace_score_row(rows[0], tokens, query)
        print()

db.close()
