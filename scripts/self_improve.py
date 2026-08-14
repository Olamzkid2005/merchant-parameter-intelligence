"""
self_improve.py — Self-improving regression harness (feature #10).

Measures the RAW engine strength WITHOUT manual aliases, so hand-added
mappings can no longer hide engine regressions:

  1. Runs every golden merchant with aliases DISABLED (alias-free mode).
  2. Reports recall@1 / recall@3 and the per-merchant alias-free accuracy.
  3. Compares recall@1 against the stored baseline
     (data/alias_free_baseline.json) — a regression fails the run.
  4. For every merchant the RAW engine fails to find, uses entity
     resolution to suggest alias candidates (family members sharing
     identifiers with the target) and drops them into the pending review
     queue — the engine proposes its own lessons instead of waiting for
     manual teaching.

Usage:
    python scripts/self_improve.py                # run + gate + suggest (default)
    python scripts/self_improve.py --no-gate      # run + suggest, don't gate
    python scripts/self_improve.py --no-suggest   # run + gate only
    python scripts/self_improve.py --record       # store the result as the
                                                  # new baseline (first run)
    python scripts/self_improve.py --top 6        # result window (default 6)

Exit codes: 0 pass, 1 regression (gate) or error.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from merchant_intelligence import MerchantSearch, config
from merchant_intelligence.entity import EntityResolver
from merchant_intelligence.golden import (GOLDEN, golden_affinity, is_correct,
                                          scored_entries)

REPORT_FILE = config.DATA_DIR / "self_improve_report.json"
BASELINE_FILE = config.DATA_DIR / "alias_free_baseline.json"
MAX_SUGGESTIONS_PER_MERCHANT = 3   # don't flood the review queue


def run_alias_free(top: int = 6) -> list:
    """Run the golden set with aliases disabled.

    Returns per-merchant rows: {query, rank, score, match_type, correct_hits}.
    """
    # Alias-free engine: same DB, same token/fuzzy scoring, no manual aliases.
    searcher = MerchantSearch(use_aliases=False)
    rows = []
    for entry in GOLDEN:
        emails = entry.get("emails", [])
        names = entry.get("names", [])
        if not emails and not names:
            continue  # no ground truth — excluded, like the benchmark
        results = searcher.search(entry["query"], limit=top, min_score=0)
        results = sorted(
            results,
            key=lambda r: (r.overall_score, golden_affinity(r, emails, names)),
            reverse=True,
        )
        found_rank = None
        best_score = 0.0
        correct_hits = 0
        for rank, r in enumerate(results, start=1):
            if is_correct(r, emails, names):
                correct_hits += 1
                if found_rank is None:
                    found_rank = rank
                best_score = max(best_score, r.overall_score)
        rows.append({
            "query": entry["query"],
            "rank": found_rank,
            "score": round(best_score, 1),
            "match_type": next((r.match_type for r in results
                                if is_correct(r, emails, names)), ""),
            "correct_hits": correct_hits,
        })
    return rows


def suggest_aliases(rows: list, max_per_merchant: int = MAX_SUGGESTIONS_PER_MERCHANT) -> list:
    """For every alias-free failure, propose alias candidates via entity
    resolution and drop them into the pending review queue.

    A failure means the RAW engine did not surface the confirmed record at
    rank 1. Its family (records sharing identifiers with the query) is the
    strongest source of alias candidates — e.g. LAGOON WATERS LTD's family
    contains the record carrying merchant20@example.com under a different
    name. Teaching those names makes the next run find it.

    Returns the list of learned (alias, canonical) pairs.
    """
    searcher = MerchantSearch(use_aliases=True)   # aliases fine for suggestion
    engine = searcher.matcher.alias_engine
    resolver = EntityResolver()
    suggested = []
    for row in rows:
        if row["rank"] is not None and row["rank"] == 1:
            continue  # already found at rank 1 alias-free — nothing to teach
        query = row["query"]
        try:
            family = resolver.family_of(query, min_members=1, max_members=50)
        except Exception:
            continue
        candidates = family.get("alias_candidates") or []
        added = 0
        for candidate in candidates:
            if added >= max_per_merchant:
                break
            cand = str(candidate).strip()
            if not cand or cand.upper() == query.upper():
                continue
            try:
                learned = engine.learn(query, cand)
            except Exception:
                continue
            if learned:
                suggested.append({"alias": query, "canonical": cand})
                added += 1
    return suggested


def aggregate(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "recall1": 0.0, "recall3": 0.0, "avg_score": 0.0,
                "found1": 0, "found3": 0}
    found1 = sum(1 for r in rows if r["rank"] == 1)
    found3 = sum(1 for r in rows if r["rank"] and r["rank"] <= 3)
    scores = [round(r["score"] / 10, 1) if r["rank"] else 0.0 for r in rows]
    return {
        "n": n,
        "recall1": round(found1 / n, 4),
        "recall3": round(found3 / n, 4),
        "avg_score": round(sum(scores) / n, 2),
        "found1": found1,
        "found3": found3,
    }


def load_baseline() -> dict:
    try:
        if BASELINE_FILE.exists():
            return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_baseline(agg: dict, top: int):
    BASELINE_FILE.write_text(json.dumps({
        "recall1": agg["recall1"],
        "recall3": agg["recall3"],
        "avg_score": agg["avg_score"],
        "n": agg["n"],
        "top": top,
    }, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Alias-free regression harness")
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--no-gate", action="store_true",
                        help="report but do not fail on regression")
    parser.add_argument("--no-suggest", action="store_true",
                        help="skip auto-suggesting alias candidates")
    parser.add_argument("--record", action="store_true",
                        help="store this run as the new baseline")
    args = parser.parse_args()

    print("=" * 72)
    print("  SELF-IMPROVING HARNESS — alias-free engine strength")
    print("=" * 72)
    print(f"  Window: top-{args.top}   Aliases: DISABLED (raw engine)\n")

    rows = run_alias_free(top=args.top)
    agg = aggregate(rows)

    print(f"  Scored merchants : {agg['n']}")
    print(f"  Recall@1         : {agg['recall1'] * 100:5.1f}%  ({agg['found1']}/{agg['n']})")
    print(f"  Recall@3         : {agg['recall3'] * 100:5.1f}%  ({agg['found3']}/{agg['n']})")
    print(f"  Avg accuracy     : {agg['avg_score']:.2f}/10")
    print("\n  Alias-free misses (the engine could not find these alone):")
    for r in rows:
        if not r["rank"]:
            print(f"      NOT FOUND    {r['query'][:60]}")
        elif r["rank"] > 1:
            print(f"      rank {r['rank']}       {r['query'][:60]}")

    # Suggestions
    suggested = []
    if not args.no_suggest:
        suggested = suggest_aliases(rows)
        if suggested:
            # ASCII-safe output (Windows cp1252 consoles choke on emoji).
            print(f"\n  [LEARN] Suggested {len(suggested)} alias(es) to the review queue:")
            for s in suggested[:10]:
                print(f"      {s['alias'][:42]:<44} -> {s['canonical'][:40]}")
            if len(suggested) > 10:
                print(f"      ... and {len(suggested) - 10} more")
        else:
            print("\n  No new alias suggestions (all alias-free hits found at rank 1).")

    # Persist report
    REPORT_FILE.write_text(json.dumps({
        "top": args.top,
        "aggregate": agg,
        "rows": rows,
        "suggested": suggested,
        "suggested_count": len(suggested),
    }, indent=2), encoding="utf-8")

    # Baseline handling
    baseline = load_baseline()
    if args.record or not baseline:
        save_baseline(agg, args.top)
        print(f"\n  Baseline recorded: recall@1={agg['recall1'] * 100:.1f}% "
              f"(data/alias_free_baseline.json)")
        print("  RESULT: PASS (baseline stored)\n")
        return

    # Window mismatch: comparing against a different top-N baseline is invalid
    if baseline.get("top") and baseline["top"] != args.top:
        print(f"\n  WARNING: baseline recorded with --top {baseline['top']} but this "
              f"run used --top {args.top} — skipping gate comparison.")
        print("  Re-run with --record to store a matching baseline.")
        print("  RESULT: PASS (gate skipped)\n")
        return

    prev1 = baseline.get("recall1", 0.0)
    prev3 = baseline.get("recall3", 0.0)
    prev_avg = baseline.get("avg_score", 0.0)
    d1 = agg["recall1"] - prev1
    d3 = agg["recall3"] - prev3
    da = agg["avg_score"] - prev_avg
    print(f"\n  Baseline recall@1 : {prev1 * 100:5.1f}%   now: {agg['recall1'] * 100:5.1f}%  "
          f"({d1:+.1f} pts)")
    print(f"  Baseline recall@3 : {prev3 * 100:5.1f}%   now: {agg['recall3'] * 100:5.1f}%  "
          f"({d3:+.1f} pts)")
    print(f"  Baseline avg score: {prev_avg:.2f}/10    now: {agg['avg_score']:.2f}/10   "
          f"({da:+.2f})")
    regressed = d1 < -1e-9 or d3 < -1e-9
    if regressed:
        print("  WARNING: an alias-free metric regressed - raw engine got worse.")
        print("  Review the changes and re-run; consider teaching aliases for:")
        for r in rows:
            if not r["rank"]:
                print(f"      {r['query'][:60]}")
        if args.no_gate:
            print("  RESULT: FAIL (gate disabled - not failing the run)\n")
            return
        print("  RESULT: FAIL\n")
        sys.exit(1)
    improved = d1 > 1e-9 or d3 > 1e-9 or da > 1e-9
    print(f"  {'IMPROVED' if improved else 'UNCHANGED'} - no regression.\n")
    print("  RESULT: PASS\n")


if __name__ == "__main__":
    main()
