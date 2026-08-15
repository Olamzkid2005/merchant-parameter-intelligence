"""
test_intent_golden.py — Golden-set novelty contract (offline).

Enforces the Phase-0 contract from docs/hybrid-semantic-intent-layer.md §7
(and scripts/phase0_baseline.py): every query in
merchant_intelligence/intent_golden.py must be a NOVEL phrasing — the
EXPECTED intent must not be reachable by a raw regex match (only the offline
~semantic/~fuzzy fallback or nothing). A query that literally contains its
own intent's pattern measures nothing, so it must fail here.

Also guards the set stays well-formed: non-empty, every entry has
query/intent/note, every expected intent is a live vocab intent (or the
injected "segment"), and no query is listed twice.

No DB, no network, no LLM — runs anywhere. Tier 2 is pinned to "off" for
the run so a user's engine_settings.json mode can't write shadow logs or
change routing during this test.

Run:  python tests/test_intent_golden.py
"""
import json
import os
import sys
from pathlib import Path

# Windows cp1252 console can't crash the suite.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence.intent_golden import INTENT_GOLDEN, intents_covered
from merchant_intelligence.tasks import analyze
from merchant_intelligence.tasks.vocab import INTENT_KEYWORDS

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


# Keep the run hermetic: the novelty check inspects raw matched patterns,
# so routing must not be influenced by a shadow/enabled mode the user may
# have saved in data/engine_settings.json.
os.environ["SEMANTIC_TIER_MODE"] = "off"

# Intents injected by the engine rather than regex-scored ("segment" is
# detected from collective phrasing, not pattern weight) — the novelty
# check, which inspects matched patterns, does not apply to them.
_INJECTED = {"segment"}
VALID = set(INTENT_KEYWORDS) | _INJECTED

# ── Routing regression snapshot ───────────────────────────────────────────
# Committed next to the golden set: query -> how the CURRENT engine routes
# it (routed | clarify | misroute | miss). The gate below asserts the live
# behavior still matches — so an intent-pattern change that silently
# re-routes a phrasing fails the suite with a per-query diff. Deliberate
# routing changes (e.g. a real Tier 1 improvement) update the snapshot via
# REBUILD_ROUTING_SNAPSHOT=1 after review.
SNAPSHOT_FILE = (Path(__file__).resolve().parent.parent
                 / "merchant_intelligence" / "intent_routing_snapshot.json")
OUTCOMES = ("routed", "clarify", "misroute", "miss")


def classify(analysis, expected):
    """How the engine routes one query (mirrors scripts/phase0_baseline.py)."""
    if not analysis.get("is_task"):
        return "miss"
    if analysis.get("clarification"):
        return "clarify"
    if analysis.get("primary") == expected:
        return "routed"
    return "misroute"


def _routing_map():
    return {e["query"]: classify(analyze(e["query"]), e["intent"])
            for e in INTENT_GOLDEN}


# ── [1] well-formedness ───────────────────────────────────────────────────
print("\n[1] golden set well-formed")
check("set is non-empty", len(INTENT_GOLDEN) >= 50, f"{len(INTENT_GOLDEN)}")
check("every entry has query + intent + note",
      all(e.get("query") and e.get("intent") and "note" in e
          for e in INTENT_GOLDEN),
      repr([e for e in INTENT_GOLDEN
            if not (e.get("query") and e.get("intent") and "note" in e)][:3]))
bad_intents = sorted({e["intent"] for e in INTENT_GOLDEN} - VALID)
check("every expected intent is a live vocab intent (or segment)",
      not bad_intents, repr(bad_intents))
seen = set()
dupes = [e["query"] for e in INTENT_GOLDEN
         if e["query"] in seen or seen.add(e["query"])]
check("no duplicate queries", not dupes, repr(dupes[:3]))
check("set covers most intents", len(intents_covered()) >= 20,
      f"{len(intents_covered())} intents")


# ── [2] novelty contract ──────────────────────────────────────────────────
# For each golden query, the EXPECTED intent must not be reachable by a raw
# regex match — only the ~semantic/~fuzzy fallback (or nothing) may fire.
# Mirrors scripts/phase0_baseline.py:_novelty_violations so a phrasing that
# literally hits its own intent's pattern fails loudly here.
print("\n[2] novelty contract — expected intent NOT reachable by raw regex")
violations = []
for entry in INTENT_GOLDEN:
    text, expected = entry["query"], entry["intent"]
    if expected in _INJECTED:
        continue
    analysis = analyze(text)
    for scored in analysis.get("intents", []):
        if scored["intent"] != expected:
            continue
        raw = [m for m in scored["matched"] if not m.startswith("~")]
        if raw:
            violations.append((text, expected, raw))
for text, expected, raw in violations:
    check(f"novel phrasing: {text!r}",
          False,
          f"expected {expected} matched raw regex {raw[:3]}")
check("all queries novel (no raw regex reaches the expected intent)",
      not violations, f"{len(violations)} violation(s)")

# ── [3] routing regression snapshot (freezes TODAY's behavior) ────────────
print("\n[3] routing regression snapshot")
actual = _routing_map()
if os.environ.get("REBUILD_ROUTING_SNAPSHOT") == "1":
    SNAPSHOT_FILE.write_text(
        json.dumps({"engine": "regex + ~semantic fallback (Tier 2 off)",
                    "rows": actual},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    check("routing snapshot regenerated (REBUILD_ROUTING_SNAPSHOT=1)", True,
          str(SNAPSHOT_FILE))
elif SNAPSHOT_FILE.exists():
    try:
        old = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8")).get("rows", {})
    except Exception:
        old = {}
    check("routing snapshot file is valid JSON", bool(old), str(SNAPSHOT_FILE))
    diffs = [(q, old[q], now) for q, now in actual.items()
             if q in old and old[q] != now]
    missing = [q for q in actual if q not in old]
    for q, prev, now in diffs:
        check(f"routing stable: {q!r}", False, f"was {prev}, now {now}")
    check("routing matches snapshot (no drift)", not diffs,
          f"{len(diffs)} drifted — run REBUILD_ROUTING_SNAPSHOT=1 only after review")
    check("snapshot covers every golden query", not missing,
          repr(missing[:3]))
else:
    # Fresh checkout with no snapshot: bootstrap it so the gate exists, then
    # the next run asserts against it.
    SNAPSHOT_FILE.write_text(
        json.dumps({"engine": "regex + ~semantic fallback (Tier 2 off)",
                    "rows": actual},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    check("routing snapshot missing — bootstrapped from current behavior",
          True, "commit it; refresh deliberately with REBUILD_ROUTING_SNAPSHOT=1")

agg = {o: sum(1 for v in actual.values() if v == o) for o in OUTCOMES}
print(f"  routing today: {agg}")

print(f"\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
