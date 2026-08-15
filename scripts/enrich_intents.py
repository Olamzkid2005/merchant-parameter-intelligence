"""
enrich_intents.py — CLI for the Tier 1 WordNet enrichment pipeline.

Thin wrapper over merchant_intelligence/tasks/enrichment.py (propose ->
curate -> apply). See the module docstring for the full design; the short
version is: WordNet proposes synonym phrases, a human approves the good
ones, applying merges them into intents.json AND regenerates vocab.py's
defaults in lockstep so the [4h] parity test can never drift.

Usage:
    python scripts/enrich_intents.py --propose          # WordNet proposals
    python scripts/enrich_intents.py --status           # list + stats
    python scripts/enrich_intents.py --approve <id> ... # curation gate
    python scripts/enrich_intents.py --approve-all      # approve everything
    python scripts/enrich_intents.py --reject <id> ...
    python scripts/enrich_intents.py --apply [--ids ...] # merge approved
    python scripts/enrich_intents.py --check            # parity gate
    python scripts/enrich_intents.py --manifest         # applied provenance
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows cp1252 consoles can't encode the ✅/📄 markers — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from merchant_intelligence.tasks import enrichment  # noqa: E402


def _print_candidates(cands) -> None:
    by_intent: dict = {}
    for c in cands:
        by_intent.setdefault(c["intent"], []).append(c)
    for intent in sorted(by_intent):
        print(f"\n── {intent} ({len(by_intent[intent])}) ──")
        for c in by_intent[intent]:
            flag = f"  [CONFLICT: {', '.join(c['conflict_with'])}]" \
                if c.get("conflict") else ""
            print(f"  {c['id']}  {c['phrase']:<42} "
                  f"<- {c['source_phrase']} ({c['synonym']})"
                  f"  [{c.get('status', 'pending')}]{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="enrich_intents.py")
    ap.add_argument("--propose", action="store_true",
                    help="WordNet-expand patterns/exemplars into pending proposals")
    ap.add_argument("--status", action="store_true",
                    help="list candidates + stats")
    ap.add_argument("--approve", nargs="*", default=None,
                    help="candidate ids to approve (curation gate)")
    ap.add_argument("--approve-all", action="store_true",
                    help="approve every pending candidate")
    ap.add_argument("--reject", nargs="*", default=None,
                    help="candidate ids to reject")
    ap.add_argument("--apply", action="store_true",
                    help="merge approved candidates into intents.json "
                         "(regenerates vocab.py defaults in lockstep)")
    ap.add_argument("--ids", nargs="*", default=None,
                    help="restrict --apply to these ids")
    ap.add_argument("--check", action="store_true",
                    help="parity gate: shipped config == code defaults")
    ap.add_argument("--manifest", action="store_true",
                    help="show applied-pattern provenance")
    args = ap.parse_args()

    if args.check:
        ok = enrichment.parity_ok()
        print("parity:", "OK" if ok else "DRIFTED — run --apply or "
              "regenerate_vocab_defaults()")
        return 0 if ok else 1

    if args.propose:
        r = enrichment.propose_candidates()
        if not r.get("ok"):
            print("✗", r.get("reason"))
            print("  hint:", r.get("wordnet", {}).get("hint"))
            return 1
        print(f"  ✅ {r['added']} new proposals "
              f"(total {r['total']}) — review with --status")
        return 0

    if args.status:
        c = enrichment.candidates()
        print(f"{c['count']} candidates "
              f"| {c['by_status']} | nltk={c['wordnet']['nltk']} "
              f"wordnet={c['wordnet']['wordnet']}")
        _print_candidates(c["candidates"])
        return 0

    if args.approve_all:
        pend = [c["id"] for c in enrichment.candidates()["candidates"]
                if c.get("status") == "pending"]
        r = enrichment.set_status(pend, "approved")
        print(f"  ✅ approved {r['changed']} pending candidates")
        return 0

    if args.approve is not None:
        r = enrichment.set_status(args.approve, "approved")
        print(f"  ✅ approved {r['changed']}")
        return 0

    if args.reject is not None:
        r = enrichment.set_status(args.reject, "rejected")
        print(f"  ✅ rejected {r['changed']}")
        return 0

    if args.apply:
        r = enrichment.apply_approved(args.ids)
        print(f"  applied {len(r['applied'])} · skipped {len(r['skipped'])}"
              f" · hot_reloaded={r['hot_reloaded']} · parity={r['parity_ok']}")
        for a in r["applied"]:
            print(f"    + {a['intent']}: \"{a['phrase']}\" "
                  f"(<- {a['source_phrase']})")
        for s in r["skipped"]:
            print(f"    - {s['intent']}: \"{s['phrase']}\" — {s['reason']}")
        return 0 if not r["skipped"] else 2

    if args.manifest:
        m = enrichment.manifest()
        print(f"{m['count']} applied auto-patterns | {m['by_intent']}")
        for e in m["entries"][-15:]:
            print(f"  {e['approved_at']}  {e['intent']:<16} "
                  f"{e['pattern']}  (from {e.get('source_phrase')})")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
