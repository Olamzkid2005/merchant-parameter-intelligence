"""
benchmark.py — Golden-set benchmark for the core search engine.

Every merchant from the user's reference sheet (with its confirmed email
and/or expected parameter-file name) is run through the engine. The engine
passes when a result's email matches a confirmed email, or its merchant_name
matches an expected name — the same ground truth the user verified by hand.

Outputs:
  - per-merchant accuracy on a scale of 1-10 (the user's original ask)
  - recall@1 / recall@3 / recall@5
  - per-tier precision (Exact / High / Possible)
  - aggregate accuracy

Run:  python benchmark.py [--top 6]

Tie-breaking: results with equal overall scores are ordered by how closely
they match the golden record (email match > name/field match > none), so a
correct hit that ties with coincidental matches at the window boundary is
NEVER ranked out by FTS/alias probe insertion order. This makes the
benchmark deterministic run-to-run (no more BEACON-style flakes).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import MerchantSearch
from merchant_intelligence.golden import GOLDEN, golden_affinity, is_correct


def main():
    parser = argparse.ArgumentParser(description="Golden-set engine benchmark")
    parser.add_argument("--top", type=int, default=6, help="results considered")
    args = parser.parse_args()

    searcher = MerchantSearch()
    scored = []          # merchants with ground truth
    unscored = []        # no ground truth (reported, excluded from aggregates)

    for entry in GOLDEN:
        query = entry["query"]
        emails = entry.get("emails", [])
        names = entry.get("names", [])
        if not emails and not names:
            unscored.append((query, entry.get("note", "")))
            continue

        results = searcher.search(query, limit=args.top, min_score=0)
        # Deterministic ordering: score desc, then golden affinity desc, so
        # a correct hit tied at the window boundary always ranks above
        # coincidental same-score matches (FTS/alias order is otherwise
        # implementation-defined and can flake run-to-run).
        results = sorted(
            results,
            key=lambda r: (r.overall_score, golden_affinity(r, emails, names)),
            reverse=True,
        )
        found_rank = None
        best_score = 0.0
        for rank, r in enumerate(results, start=1):
            if is_correct(r, emails, names):
                if found_rank is None:
                    found_rank = rank
                best_score = max(best_score, r.overall_score)
        scored.append({
            "query": query,
            "rank": found_rank,
            "score": round(best_score, 1),
            "match_type": next((r.match_type for r in results
                                if is_correct(r, emails, names)), ""),
            "top": args.top,
        })

    # ── Per-merchant report ──────────────────────────────────────────────
    print("=" * 78)
    print("  ENGINE BENCHMARK — golden set (accuracy /10)")
    print("=" * 78)
    for s in scored:
        acc = round(s["score"] / 10, 1) if s["rank"] else 0.0
        rank_txt = f"@{s['rank']}" if s["rank"] else "NOT FOUND"
        tier = s["match_type"] or "-"
        print(f"  {acc:4.1f}/10  {rank_txt:<10} {tier:<16} {s['query'][:44]}")
    if unscored:
        print("\n  (no ground truth, excluded from stats)")
        for q, note in unscored:
            print(f"      {q[:60]}")

    # ── Aggregates ───────────────────────────────────────────────────────
    n = len(scored)
    if n == 0:
        print("\n  No scored merchants.")
        return
    recall = {k: sum(1 for s in scored if s["rank"] and s["rank"] <= k) / n
              for k in (1, 3, args.top)}
    accs = [round(s["score"] / 10, 1) if s["rank"] else 0.0 for s in scored]
    avg_acc = sum(accs) / n

    # Tier precision: of results in each tier, how many were correct.
    from collections import defaultdict
    tier_hits = defaultdict(lambda: [0, 0])  # tier -> [correct, total]
    for entry in GOLDEN:
        emails = entry.get("emails", [])
        names = entry.get("names", [])
        if not emails and not names:
            continue
        for r in searcher.search(entry["query"], limit=args.top, min_score=0):
            tier_hits[r.match_type][1] += 1
            if is_correct(r, emails, names):
                tier_hits[r.match_type][0] += 1

    print("\n" + "=" * 78)
    print("  AGGREGATES")
    print("=" * 78)
    print(f"  Scored merchants          : {n}")
    print(f"  Avg accuracy (/10)        : {avg_acc:.2f}")
    for k in (1, 3, args.top):
        print(f"  Recall@{k:<3}                 : {recall[k]*100:5.1f}%")
    print(f"  Tier precision (correct/total):")
    for tier in ("Exact Match", "High Confidence", "Possible Match", "Low Confidence"):
        if tier in tier_hits:
            c, t = tier_hits[tier]
            pct = c / t * 100 if t else 0
            print(f"      {tier:<16} {c:>4}/{t:<4} = {pct:5.1f}%")

    # Flag merchants still missing so the user knows what to teach the engine
    missing = [s["query"] for s in scored if not s["rank"]]
    if missing:
        print("\n  NOT FOUND (teach the engine or add aliases):")
        for m in missing:
            print(f"      {m}")
    else:
        print("\n  Every scored merchant was found in top "
              f"{args.top}. SUCCESS!\n")


if __name__ == "__main__":
    main()
