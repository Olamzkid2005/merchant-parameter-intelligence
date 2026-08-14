"""
Find the exact bug in _score_field that makes ALL fields score 100.
"""
import sys
sys.path.insert(0, r"C:\Users\David.Olamijulo\downloads\parameter")
from difflib import SequenceMatcher
from merchant_intelligence import MerchantMatcher, DatabaseManager, config

db = DatabaseManager()
matcher = MerchantMatcher(db)

# Get a merchant that has field data
rows = db.search_by_column("merchant_name", "PETROCAM", limit=1)
row = rows[0]
record = dict(row)

query = "CRANE FIELD INTERNMATIONAL SCHOOL JEDDO"
tokens = MerchantMatcher._tokenise(query)

print("=" * 70)
print("TRACING _score_field FOR EACH FIELD")
print("=" * 70)

for field in config.FIELD_WEIGHTS:
    field_value = record.get(field)
    if not field_value:
        print(f"\n{field}: (empty) -> 0")
        continue
    
    fv = str(field_value).upper().strip()
    rq = query.upper().strip()
    
    print(f"\n{field}: {str(field_value)[:60]}")
    print(f"  fv = '{fv}'")
    print(f"  rq = '{rq}'")
    
    # Step 1: Exact match
    if fv == rq:
        print(f"  STEP 1 (exact match) -> 100")
        continue
    
    # Step 2: Substring
    if rq in fv:
        print(f"  STEP 2a (rq in fv) -> 90")
        continue
    if fv in rq:
        print(f"  STEP 2b (fv in rq) -> 80")
        continue
    
    # Step 3: Token-based
    field_tokens = MerchantMatcher._tokenise(fv)
    print(f"  field_tokens = {field_tokens}")
    if not field_tokens or not tokens:
        print(f"  -> 0 (no field_tokens or tokens)")
        continue
    
    query_tokens = [t.upper() for t in tokens]
    query_set = set(query_tokens)
    field_set = set(t.upper() for t in field_tokens)
    exact_overlap = query_set & field_set
    exact_match_ratio = len(exact_overlap) / len(query_set)
    print(f"  query_set = {query_set}")
    print(f"  field_set = {field_set}")
    print(f"  exact_overlap = {exact_overlap}")
    print(f"  exact_match_ratio = {exact_match_ratio}")
    
    # Count fuzzy matches
    fuzzy_match_count = 0
    for qt in query_tokens:
        for ft in field_tokens:
            ft_upper = ft.upper()
            if qt == ft_upper:
                continue
            sim = SequenceMatcher(None, qt, ft_upper).ratio()
            if sim >= 0.60:
                fuzzy_match_count += 1
                print(f"  fuzzy: {qt} ~ {ft_upper} = {sim:.4f}")
                break
    
    fuzzy_match_ratio = fuzzy_match_count / len(query_set) if query_set else 0
    print(f"  fuzzy_match_ratio = {fuzzy_match_ratio}")
    
    if exact_match_ratio >= 0.5:
        print(f"  -> exact_match path")
    elif fuzzy_match_ratio >= 0.3:
        print(f"  -> fuzzy_match path")
    else:
        print(f"  -> else path (full-string sim)")

db.close()
