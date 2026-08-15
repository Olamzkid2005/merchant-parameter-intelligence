"""
test_enrichment.py — Tier 1 WordNet enrichment pipeline (propose -> curate
-> apply), hermetic and fully offline.

The suite fakes the WordNet layer (deterministic synonym map, no corpus
needed) and points every write at a temp directory:

  - MERCHANT_INTENTS_CONFIG -> temp intents.json
  - MERCHANT_INTENTS_VOCAB  -> temp COPY of vocab.py (so apply's lockstep
    regeneration is exercised without touching the real file)
  - config.DATA_DIR         -> temp dir (candidates / manifest / exemplars)

The shipped intents.json and vocab.py are never modified — the final checks
assert the real shipped parity still holds after the suite runs.

Run:  python -X utf-8 tests/test_enrichment.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT))
from merchant_intelligence import config
from merchant_intelligence.tasks import enrichment, vocab

checks = 0
fails = 0


def check(name, cond, detail=""):
    global checks, fails
    checks += 1
    mark = "ok" if cond else "FAIL"
    if not cond:
        fails += 1
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


# ── Fake WordNet: deterministic synonyms for the four content words used ──
FAKE_SYNONYMS = {
    "account": ["bill", "ledger"],
    "holder": ["keeper", "owner"],
    "name": ["title", "label"],
    "title": ["caption", "heading"],
}


def fake_synonyms(word):
    return FAKE_SYNONYMS.get(word, [])


def _ph_equal(a, b):
    """Escaped-space vs literal-space tolerant pattern comparison (mirrors
    enrichment._matches_phrase without importing module internals)."""
    return (a.lower().replace("\\ ", " ").replace("\\b", "")
            == b.lower().replace("\\ ", " ").replace("\\b", ""))


# ── Setup: temp config + temp vocab copy + temp DATA_DIR ─────────────────
tmp = Path(tempfile.mkdtemp(prefix="enrich_test_"))
_tmp_intents = tmp / "intents.json"
_tmp_vocab = tmp / "vocab_copy.py"
_tmp_data = tmp / "data"

_tmp_intents.write_text(json.dumps({
    "intents": {
        "account_name": {
            "patterns": [{"pattern": r"\baccount holder\b", "weight": 5},
                         {"pattern": r"\baccount names?\b", "weight": 6}],
            "keywords": ["account holder", "account name"],
        },
        "email": {
            "patterns": [{"pattern": r"\be[- ]?mails?\b", "weight": 5}],
            "keywords": ["email"],
        },
    },
}), encoding="utf-8")

shutil.copy2(ROOT / "merchant_intelligence" / "tasks" / "vocab.py", _tmp_vocab)
_tmp_data.mkdir()
(_tmp_data / "exemplars.json").write_text(json.dumps({
    "intents": {"account_name": ["account holder", "account title"]},
}), encoding="utf-8")

_old_config_env = os.environ.get("MERCHANT_INTENTS_CONFIG")
_old_vocab_env = os.environ.get("MERCHANT_INTENTS_VOCAB")
_old_data_dir = config.DATA_DIR
os.environ["MERCHANT_INTENTS_CONFIG"] = str(_tmp_intents)
os.environ["MERCHANT_INTENTS_VOCAB"] = str(_tmp_vocab)
config.DATA_DIR = _tmp_data

import importlib  # noqa: E402
importlib.reload(vocab)
enrichment._wordnet_synonyms = fake_synonyms  # type: ignore[assignment]

try:
    # ── Literal-run extraction ────────────────────────────────────────────
    print("\n[1] literal-run extraction")
    check("plain \\b phrase", enrichment._literal_runs(r"\bstatic account\b") == "static account")
    check("\\b escape 'b' never glues", enrichment._literal_runs(r"\baccount holder\b") == "account holder")
    check("no baccount garbage", "baccount" not in (enrichment._literal_runs(r"\baccount manager\b") or ""))
    check("regex variants collapse", enrichment._literal_runs(r"\be[- ]?mails?\b") == "mails")
    check("digit classes dropped", enrichment._literal_runs(r"\btop \d+\b") == "top")
    check("no literal run -> None", enrichment._literal_runs(r"\b\d+\b") is None)

    # ── Expansion (fake WordNet) ──────────────────────────────────────────
    print("\n[2] phrase expansion")
    same, exact = enrichment._live_pattern_index()
    cands = enrichment._expand_phrase("account_name", "account holder", same, exact)
    phrases = {c["phrase"] for c in cands}
    check("replaces each content word", phrases == {"bill holder", "ledger holder",
                                                    "account keeper", "account owner"}, str(phrases))
    check("provenance fields present", all(
        c.get("source_phrase") == "account holder" and c.get("source_word")
        and c.get("synonym") and c.get("status") == "pending"
        for c in cands))
    check("stable ids", len({c["id"] for c in cands}) == len(cands)
          and cands[0]["id"] == enrichment._candidate_id("account_name", cands[0]["phrase"]))
    check("novelty: existing pattern excluded",
          all("account name" != c["phrase"] for c in cands))
    check("no conflict by default", all(not c["conflict"] for c in cands))

    # A candidate that already exists as another intent's pattern is flagged.
    same2 = dict(same)
    same2["email"] = [r"\bbill holder\b"]
    exact2 = {k: list(v) for k, v in exact.items()}
    exact2.setdefault(r"\bbill holder\b", []).append("email")
    cands2 = enrichment._expand_phrase("account_name", "account holder", same2, exact2)
    hit = [c for c in cands2 if c["phrase"] == "bill holder"]
    check("cross-intent conflict flagged", bool(hit) and hit[0]["conflict"]
          and "email" in hit[0]["conflict_with"])

    # ── Propose (idempotent, preserves statuses) ──────────────────────────
    print("\n[3] propose")
    r1 = enrichment.propose_candidates()
    check("propose ok", r1.get("ok") is True)
    n1 = enrichment.candidates()["count"]
    check("proposals written", n1 > 0, f"n={n1}")
    # Approve one, then re-propose — status must survive and no dupes added.
    first_id = enrichment.candidates()["candidates"][0]["id"]
    enrichment.set_status([first_id], "approved")
    r2 = enrichment.propose_candidates()
    check("re-propose adds nothing new", r2["added"] == 0, str(r2))
    check("re-propose total unchanged", enrichment.candidates()["count"] == n1)
    st = {c["id"]: c["status"] for c in enrichment.candidates()["candidates"]}
    check("approval survived re-propose", st.get(first_id) == "approved")

    # ── Curation gate ─────────────────────────────────────────────────────
    print("\n[4] curation gate")
    r = enrichment.set_status([first_id], "rejected")
    check("reject flips status", r.get("changed") == 1)
    r = enrichment.set_status([first_id], "bogus")
    check("bad status refused", r.get("ok") is False)
    st = {c["id"]: c["status"] for c in enrichment.candidates()["candidates"]}
    check("still rejected after bad call", st.get(first_id) == "rejected")

    # ── Apply: merge + lockstep + exemplars + manifest ────────────────────
    print("\n[5] apply")
    # Approve exactly two candidates for account_name.
    cands_now = enrichment.candidates()["candidates"]
    account_cands = [c for c in cands_now if c["intent"] == "account_name"
                     and c.get("status") == "pending"][:2]
    ids = [c["id"] for c in account_cands]
    enrichment.set_status(ids, "approved")
    r = enrichment.apply_approved()
    check("applied both", len(r["applied"]) == 2, str(r["applied"]))
    check("parity after apply", r["parity_ok"] is True)

    # intents.json now carries both weight-2 patterns (re.escape semantics:
    # Python 3.7+ escapes spaces too, so the comparison must mirror it).
    live = vocab.INTENT_PATTERNS.get("account_name", [])
    for c in account_cands:
        pat = r"\b" + re.escape(c["phrase"]) + r"\b"
        check(f"config has {c['phrase']!r} (w=2)",
              any(p == pat and w == 2 for p, w in live), str(live))

    # The temp vocab COPY was regenerated in lockstep — verify the FILE
    # semantically (it cannot be imported standalone: relative imports).
    vtext = _tmp_vocab.read_text(encoding="utf-8")
    vstart = vtext.index("_DEFAULT_INTENT_PATTERNS")
    vbrace = vtext.index("{", vstart)
    vclose = re.search(r"^}", vtext[vbrace:], re.M)
    file_defaults = ast.literal_eval(vtext[vbrace:vbrace + vclose.end()])
    check("temp vocab file defaults == live patterns",
          file_defaults == vocab.INTENT_PATTERNS)
    for c in account_cands:
        pat = r"\b" + re.escape(c["phrase"]) + r"\b"
        check(f"temp vocab has {c['phrase']!r}",
              any(_ph_equal(p, pat) for p, _w in file_defaults.get("account_name", [])))

    # Exemplars appended.
    ex = json.loads((_tmp_data / "exemplars.json").read_text(encoding="utf-8"))
    check("exemplars got approved phrases",
          all(c["phrase"] in ex["intents"]["account_name"] for c in account_cands))

    # Manifest provenance recorded.
    manifest = json.loads((_tmp_data / "auto_pattern_manifest.json").read_text(encoding="utf-8"))
    check("manifest entries", len(manifest) == 2
          and all(e["source"] == "wordnet" and e["weight"] == 2
                  and e["intent"] == "account_name" for e in manifest))

    # Candidates marked applied; re-apply is a no-op.
    st = {c["id"]: c["status"] for c in enrichment.candidates()["candidates"]}
    check("applied marked", all(st.get(i) == "applied" for i in ids))
    r = enrichment.apply_approved()
    check("re-apply idempotent", len(r["applied"]) == 0, str(r["applied"]))

    # ── Conflict guard: a flagged candidate is never merged ───────────────
    print("\n[6] conflict guard")
    # Fresh pattern index + an injected email pattern that collides with a
    # candidate, and a fake synonym that produces that candidate.
    same3 = {i: [p for p, _w in pats]
             for i, pats in vocab.INTENT_PATTERNS.items()}
    same3["email"] = same3.get("email", []) + [r"\bzzz holder\b"]
    exact3 = {k: list(v) for k, v in exact.items()}
    exact3.setdefault(r"\bzzz holder\b", []).append("email")
    enrichment._wordnet_synonyms = lambda w: {  # type: ignore[assignment]
        "account": ["bill", "ledger", "zzz"],
        "holder": ["keeper", "owner"],
    }.get(w, [])
    cands3 = enrichment._expand_phrase("account_name", "account holder", same3, exact3)
    confl = [c for c in cands3 if c["phrase"] == "zzz holder"]
    check("conflict candidate produced", bool(confl))
    if confl:
        # Add it to the store (propose would, but this candidate came from an
        # injected pattern) then approve + apply — the flagged conflict must
        # refuse the merge.
        store = enrichment._read_candidates()
        if confl[0]["id"] not in {c["id"] for c in store}:
            store.append(confl[0])
        enrichment._write_candidates(store, "test")
        enrichment.set_status([confl[0]["id"]], "approved")
        r = enrichment.apply_approved()
        check("conflicting candidate skipped",
              any(s["phrase"] == "zzz holder" and s["reason"]
                  for s in r["skipped"]), str(r["skipped"]))

    # ── Lockstep via feedback.apply_pattern (the existing drift bug) ──────
    print("\n[7] feedback.apply_pattern lockstep")
    from merchant_intelligence import feedback
    spec_out = feedback.apply_pattern("sample wording", "email")
    check("pattern applied", spec_out is not None)
    check("vocab regenerated in lockstep",
          vocab.INTENT_PATTERNS == vocab._DEFAULT_INTENT_PATTERNS)
    check("new pattern live", any(
        p == r"\bsample\s+wording\b" for p, _w in vocab.INTENT_PATTERNS.get("email", [])))
finally:
    # ── Restore everything, then assert the SHIPPED files never moved ─────
    if _old_config_env is None:
        os.environ.pop("MERCHANT_INTENTS_CONFIG", None)
    else:
        os.environ["MERCHANT_INTENTS_CONFIG"] = _old_config_env
    if _old_vocab_env is None:
        os.environ.pop("MERCHANT_INTENTS_VOCAB", None)
    else:
        os.environ["MERCHANT_INTENTS_VOCAB"] = _old_vocab_env
    config.DATA_DIR = _old_data_dir
    importlib.reload(vocab)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[8] shipped files untouched")
check("shipped config == shipped defaults",
      vocab.INTENT_PATTERNS == vocab._DEFAULT_INTENT_PATTERNS)
check("wordnet still available", enrichment.wordnet_available())

print(f"\n{'=' * 50}")
print(f"  RESULT: {checks - fails}/{checks} checks passed"
      + ("  ✅" if fails == 0 else "  ❌"))
print(f"{'=' * 50}")
sys.exit(1 if fails else 0)
