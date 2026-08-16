"""
phase0_baseline.py — Phase 0 baseline measurement for the hybrid semantic
intent layer (docs/hybrid-semantic-intent-layer.md, §7).

Runs the held-out novel-phrasings set (merchant_intelligence/intent_golden.py)
through the CURRENT engine — regex patterns + the existing ~semantic/~fuzzy
fallback — and classifies how each query would actually route today:

    routed    detect_task produced a task whose intent == expected (no clarify)
    clarify   the request would have triggered the clarification card
    misroute  detect_task produced a task with a DIFFERENT intent
    miss      detect_task produced no task at all (falls through to search)

This is the "before" number the semantic layer's Phase 2 enable gate compares
against, and the Phase 0 set doubles as the ongoing intent golden set.

NOVELTY CONTRACT — the harness verifies the set before measuring:
    For each query, the EXPECTED intent must not be reachable by a raw regex
    match (only ~semantic/~fuzzy or nothing). A phrasing that literally hits
    its own intent's pattern measures nothing and is reported as a violation
    (exit code 1, measurement still printed).

Usage:
    python scripts/phase0_baseline.py            # report + data/ snapshot
    python scripts/phase0_baseline.py --detail   # also print every query row
    python scripts/phase0_baseline.py --json out.json
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows consoles default to cp1252, which cannot encode the ✅/⚠ markers
# used in the report — force UTF-8 output so the script runs anywhere.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from merchant_intelligence.intent_golden import INTENT_GOLDEN, intents_covered
from merchant_intelligence.tasks import analyze
from merchant_intelligence.tasks import semantic as tier2
from merchant_intelligence.tasks.vocab import INTENT_PATTERNS

OUTCOMES = ("routed", "clarify", "misroute", "miss")

# Intents that are injected by the engine rather than regex-scored — the
# novelty check (which inspects matched patterns) does not apply to them.
_INJECTED = {"segment"}

# Auto-synonym patterns (enrichment WEIGHT_SYNONYM = 2) are the sanctioned
# Tier-1 expansion path (design doc §4): a golden phrasing absorbed by one
# is no longer novel BY DESIGN, so it is not a novelty violation. The
# contract applies to hand-authored patterns (weight >= 3). Mirrors
# tests/test_intent_golden.py.
AUTO_WEIGHT = 2
_AUTO_PATTERNS = {
    intent: {p for p, w in pats if w == AUTO_WEIGHT}
    for intent, pats in INTENT_PATTERNS.items()
}


def _novelty_violations(text: str, expected: str, analysis: dict) -> list:
    """Hand-authored raw-regex matches for the EXPECTED intent (novelty
    violations). Auto-synonym matches (weight 2) don't count — absorbing a
    novel phrasing into Tier 1 is the enrichment layer's purpose."""
    if expected in _INJECTED:
        return []
    for entry in analysis.get("intents", []):
        if entry["intent"] != expected:
            continue
        raw = [m for m in entry["matched"]
               if not m.startswith("~") and m not in _AUTO_PATTERNS.get(expected, set())]
        if raw:
            return raw
    return []


def _other_regex_hits(analysis: dict) -> list:
    """Intents that scored from a RAW regex match (ambiguity context)."""
    out = []
    for entry in analysis.get("intents", []):
        if any(not m.startswith("~") for m in entry["matched"]):
            out.append(f"{entry['intent']}@{entry['confidence']}")
    return out


def classify(analysis: dict, expected: str) -> str:
    """One of OUTCOMES: how the current engine routes this query."""
    if not analysis.get("is_task"):
        return "miss"
    if analysis.get("clarification"):
        return "clarify"
    if analysis.get("primary") == expected:
        return "routed"
    return "misroute"


def run(detail: bool = False, tier2_preview: bool = False) -> dict:
    rows = []
    violations = []
    for entry in INTENT_GOLDEN:
        text, expected = entry["query"], entry["intent"]
        analysis = analyze(text)
        outcome = classify(analysis, expected)
        viol = _novelty_violations(text, expected, analysis)
        if viol:
            violations.append((text, expected, viol))
        row = {
            "query": text,
            "expected": expected,
            "outcome": outcome,
            "primary": analysis.get("primary"),
            "confidence": analysis.get("confidence"),
            "clarification": bool(analysis.get("clarification")),
            "names": analysis.get("names", []),
            "segment": analysis.get("segment", ""),
            "other_regex_hits": _other_regex_hits(analysis),
        }
        if tier2_preview:
            # Phase-1 preview: what would Tier 2 (active encoder) have decided
            # for this request? Masked against the parsed task so the vector
            # sees intent language only (design doc §11).
            t2 = tier2.resolve(text, analysis.get("task") or None)
            row["tier2"] = {
                "intent": (t2 or {}).get("intent"),
                "exemplar": (t2 or {}).get("exemplar"),
                "confidence": (t2 or {}).get("confidence"),
                "would_act": bool((t2 or {}).get("would_act")),
            }
        rows.append(row)

    # ── Report ────────────────────────────────────────────────────────────
    print("=" * 72)
    print("  PHASE 0 BASELINE — current engine vs novel phrasings")
    print("=" * 72)
    if violations:
        print("\n  ⚠ NOVELTY VIOLATIONS (expected intent reachable by raw regex —")
        print("    these phrasings measure nothing; replace them):")
        for text, expected, matched in violations:
            print(f"    - [{expected}] {text!r}")
            print(f"        matched: {matched[:3]}")
        print()
    else:
        print("\n  ✅ Novelty contract satisfied — no expected intent is")
        print("     reachable by a raw regex match.\n")

    by_intent = {}
    for r in rows:
        bucket = by_intent.setdefault(r["expected"], {o: 0 for o in OUTCOMES})
        bucket[r["outcome"]] += 1

    print(f"  {'intent':<16} {'n':>3} {'routed':>7} {'clarify':>7} "
          f"{'misroute':>8} {'miss':>5}  {'routed%':>7}")
    print("  " + "-" * 64)
    for intent in intents_covered():
        b = by_intent[intent]
        n = sum(b.values())
        pct = 100 * b["routed"] / n if n else 0.0
        print(f"  {intent:<16} {n:>3} {b['routed']:>7} {b['clarify']:>7} "
              f"{b['misroute']:>8} {b['miss']:>5}  {pct:>6.0f}%")

    total = len(rows)
    agg = {o: sum(by_intent[i][o] for i in by_intent) for o in OUTCOMES}
    routed_pct = 100 * agg["routed"] / total
    print("  " + "-" * 64)
    print(f"  {'TOTAL':<16} {total:>3} {agg['routed']:>7} {agg['clarify']:>7} "
          f"{agg['misroute']:>8} {agg['miss']:>5}  {routed_pct:>6.0f}%")
    print(f"\n  Miss rate (falls through to plain search):  "
          f"{100 * agg['miss'] / total:.0f}%")
    print(f"  Misroute rate (wrong pipeline, no question asked): "
          f"{100 * agg['misroute'] / total:.0f}%")
    print(f"  Clarify rate (engine asks instead of deciding):   "
          f"{100 * agg['clarify'] / total:.0f}%")

    if tier2_preview:
        print("\n  " + "-" * 72)
        print("  TIER-2 PREVIEW (active encoder: " + tier2._make_encoder().id + ")")
        print("  -" * 72)
        correct = sum(1 for r in rows
                      if r["tier2"]["intent"] == r["expected"])
        act = sum(1 for r in rows if r["tier2"]["would_act"])
        correct_act = sum(1 for r in rows
                          if r["tier2"]["would_act"]
                          and r["tier2"]["intent"] == r["expected"])
        # Would Tier 2 fix the current misses/misroutes? (the §7 promise)
        fixable = sum(1 for r in rows
                      if r["outcome"] in ("miss", "misroute")
                      and r["tier2"]["would_act"]
                      and r["tier2"]["intent"] == r["expected"])
        troubled = [r for r in rows if r["outcome"] in ("miss", "misroute")]
        print(f"  Tier-2 top-1 matches expected intent: {correct}/{len(rows)}"
              f" ({100 * correct // len(rows)}%)")
        print(f"  Would act (clears threshold+margin): {act}/{len(rows)}"
              f" ({100 * act // len(rows)}%)")
        print(f"  Would act AND correct: {correct_act}/{len(rows)}"
              f" ({100 * correct_act // len(rows)}%)")
        print(f"  Of today's {len(troubled)} misses/misroutes, Tier-2 would"
              f" correct {fixable} (act + right intent)")
        wrong = [r for r in rows if r["tier2"]["would_act"]
                 and r["tier2"]["intent"] != r["expected"]]
        if wrong:
            print(f"  ⚠ {len(wrong)} would-act decisions point at the WRONG intent:")
            for r in wrong[:8]:
                print(f"     - [{r['expected']}] {r['query']!r} -> "
                      f"tier2={r['tier2']['intent']} "
                      f"conf={r['tier2']['confidence']} "
                      f"(exemplar {r['tier2']['exemplar']!r})")

    if detail:
        print("\n  ── per-query ──")
        for r in rows:
            extra = []
            if r["other_regex_hits"]:
                extra.append("regex-hits:" + ",".join(r["other_regex_hits"]))
            if r["segment"]:
                extra.append(f"segment={r['segment']!r}")
            if r["names"]:
                extra.append(f"names={r['names']}")
            if tier2_preview:
                t2 = r["tier2"]
                extra.append("tier2=" + (f"{t2['intent']}@{t2['confidence']}"
                                          if t2["intent"] else "-")
                             + ("(act)" if t2["would_act"] else ""))
            note = ("  [" + "; ".join(extra) + "]") if extra else ""
            print(f"  {r['outcome']:<8} {r['expected']:<16} conf={r['confidence']:<3}"
                  f" primary={str(r['primary']):<16} {r['query']!r}{note}")

    return {
        "snapshot_ts": __import__("time").time(),
        "engine": "current (regex + ~semantic fallback, Tier 2 off)",
        "total": total,
        "aggregate": agg,
        "routed_pct": round(routed_pct, 1),
        "miss_pct": round(100 * agg["miss"] / total, 1),
        "misroute_pct": round(100 * agg["misroute"] / total, 1),
        "clarify_pct": round(100 * agg["clarify"] / total, 1),
        "by_intent": by_intent,
        "novelty_violations": [
            {"query": t, "expected": e, "matched": m}
            for t, e, m in violations
        ],
        "tier2_preview": tier2_preview,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", action="store_true",
                    help="print every query row")
    ap.add_argument("--tier2", action="store_true",
                    help="also run the Tier-2 semantic layer (Phase-1 preview)")
    ap.add_argument("--json", default=str(PROJECT_ROOT / "data" / "phase0_baseline.json"),
                    help="JSON snapshot path (default: data/phase0_baseline.json)")
    args = ap.parse_args()

    report = run(detail=args.detail, tier2_preview=args.tier2)
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  📄 snapshot -> {out}")
    return 1 if report["novelty_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
