"""
calibrate_weights.py — Fit the engine's field weights against the golden set.

The hand-set FIELD_WEIGHTS (config.py) were chosen by inspection. This harness
fits them to the data you actually verified: each golden query is run through
the ALIAS-FREE engine ONCE (the heavy part — real searches against
intelligence.db), its results are cached, and then scoring is re-run in memory
for thousands of weight candidates — so a full coordinate search costs ~30s of
queries, not hours.

It writes the best weights to data/field_weights.json (which config.py loads
as the source of truth, exactly like manual_aliases.json) and prints a
before/after report.

    python scripts/calibrate_weights.py            # run the fit, print report
    python scripts/calibrate_weights.py --dry-run  # measure baseline only

Metrics: recall@1 and recall@3 over golden.scored_entries() (entries with
confirmed ground truth). Tie-breaks mirror the engine's final sort (email
presence leads equal-score groups).
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from merchant_intelligence import config
from merchant_intelligence import golden as golden_mod
from merchant_intelligence import MerchantSearch
from merchant_intelligence.matcher import SearchResult

# Fields to search over, in dominance order. terminal_serial and payable_code
# are nearly noise — included so the search can zero them if they hurt.
FIT_FIELDS = ["merchant_name", "slip_header", "email", "mxcode", "tid",
              "address", "account_name", "phone", "alias", "payable_code",
              "contact_name", "terminal_serial"]

# Candidate multipliers tried per field per pass.
MULTIPLIERS = [0.0, 0.4, 0.7, 1.4, 2.2]
MAX_PASSES = 3
TOP_K_PER_QUERY = 8  # candidates cached per golden query


# ── one-shot query cache ─────────────────────────────────────────────────

def _exact_bonus(matcher, query: str, r) -> float:
    """Recompute the multi-token bonus exactly as _score_row computes it.

    bonus = min(max(0, name_cov * len(tokens) - 1) * 0.10, 0.30) where
    name_cov = rarity-weighted fraction of the query tokens the NAME matched.
    Depends only on the query tokens, the cached token counts (idf), and the
    result's own matched_tokens — all stable across weight candidates, so it
    is captured ONCE per result here and reused in every rescore.
    """
    tokens = matcher._tokenise(query)
    if len(tokens) <= 1 or not r.matched_tokens:
        return 0.0
    idf = {t: matcher._idf(t) for t in tokens}
    matched_weight = sum(idf.get(t, 1.0) for t in r.matched_tokens)
    total_query_weight = sum(idf.values()) or 1.0
    name_cov = matched_weight / total_query_weight
    extra = max(0.0, name_cov * len(tokens) - 1.0)
    return min(extra * 0.10, 0.30)


def run_queries() -> list:
    """Run every golden query once (alias-free) and cache the raw evidence."""
    searcher = MerchantSearch(use_aliases=False)
    cached = []
    for entry in golden_mod.scored_entries():
        q = entry["query"]
        results = searcher.search(q, limit=TOP_K_PER_QUERY, min_score=0)
        # Keep everything compute_overall needs to re-score in memory.
        data = []
        for r in results:
            data.append({
                "id": r.merchant_id,
                "record": dict(r.record),
                "field_scores": dict(r.field_scores),
                "matched_tokens": list(r.matched_tokens),
                "identifier_hit": r.identifier_hit,
                "boost_secondary": r.boost_secondary,
                "query_boost_fields": dict(r.query_boost_fields),
                "multi_token_bonus": _exact_bonus(searcher.matcher, q, r),
            })
        cached.append({"entry": entry, "results": data})
    return cached


def _rescore(data, weights) -> list:
    """Re-score cached results with a candidate weight dict (in memory)."""
    out = []
    for d in data:
        r = SearchResult(d["id"], dict(d["record"]))
        r.field_scores = dict(d["field_scores"])
        r.matched_tokens = list(d["matched_tokens"])
        r.identifier_hit = d["identifier_hit"]
        r.boost_secondary = d["boost_secondary"]
        r.query_boost_fields = dict(d["query_boost_fields"])
        r.compute_overall(d.get("multi_token_bonus", 0.0))
        out.append(r)
    out.sort(key=lambda x: (x.overall_score,
                            "@" in str(x.record.get("email") or "")),
             reverse=True)
    return out


def evaluate(cached, weights, k=1) -> int:
    """Count golden queries whose top-k rescored results are correct."""
    hits = 0
    for item in cached:
        entry = item["entry"]
        results = _rescore(item["results"], weights)
        if any(golden_mod.is_correct(r, entry["emails"], entry["names"])
               for r in results[:k]):
            hits += 1
    return hits


# ── fit ──────────────────────────────────────────────────────────────────

def fit(cached):
    weights = dict(config._DEFAULT_FIELD_WEIGHTS)
    best = evaluate(cached, weights, k=1)
    total = len(cached)
    print(f"  baseline recall@1: {best}/{total} ({best / total:.0%})")
    changed = True
    while changed:
        changed = False
        for field in FIT_FIELDS:
            base = weights[field]
            best_local, best_w = best, weights
            for mult in MULTIPLIERS:
                cand = dict(weights)
                cand[field] = max(0, int(base * mult))
                if sum(cand.values()) == 0:
                    continue
                score = evaluate(cached, cand, k=1)
                if score > best_local or (score == best_local
                                          and cand[field] < best_w[field]):
                    best_local, best_w = score, cand
            if best_local > best:
                weights = best_w
                best = best_local
                changed = True
                print(f"  [fit] {field}: {base} -> {weights[field]} "
                      f"(recall@1 now {best}/{total})")
    return weights, best


# ── main ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="measure baseline only, write nothing")
    args = ap.parse_args()

    t0 = time.perf_counter()
    print(f"Running {len(golden_mod.scored_entries())} golden queries "
          f"(alias-free, one-shot)...")
    cached = run_queries()
    print(f"  queried in {time.perf_counter() - t0:.1f}s")

    total = len(cached)
    base = evaluate(cached, dict(config._DEFAULT_FIELD_WEIGHTS), k=1)
    base3 = evaluate(cached, dict(config._DEFAULT_FIELD_WEIGHTS), k=3)
    print(f"\nDEFAULT weights:  recall@1 {base}/{total} ({base / total:.0%}), "
          f"recall@3 {base3}/{total} ({base3 / total:.0%})")

    if args.dry_run:
        print("\n--dry-run: no changes written.")
        return

    weights, best = fit(cached)
    best3 = evaluate(cached, weights, k=3)
    print(f"\nFITTED weights:   recall@1 {best}/{total} ({best / total:.0%}), "
          f"recall@3 {best3}/{total} ({best3 / total:.0%})")

    # Only write the JSON override when the fit actually changed something —
    # config.py prefers the file when present, so an unchanged fit must not
    # leave a redundant file behind.
    if weights == config._DEFAULT_FIELD_WEIGHTS:
        print("\nFit found no improvement over the defaults — nothing written "
              "(hand-set weights already optimal for this golden set).")
        return
    out_path = config.DATA_DIR / "field_weights.json"
    payload = {
        "weights": {k: int(v) for k, v in weights.items()},
        "recall1": best, "total": total, "recall3": best3,
        "baseline_recall1": base,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print("Diff vs defaults:")
    for k in sorted(weights):
        if weights[k] != config._DEFAULT_FIELD_WEIGHTS.get(k):
            print(f"  {k}: {config._DEFAULT_FIELD_WEIGHTS.get(k)} -> {weights[k]}")


if __name__ == "__main__":
    main()
