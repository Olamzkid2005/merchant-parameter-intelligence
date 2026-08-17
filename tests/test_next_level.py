"""
test_next_level.py — Tests for the two far-out engine features:

  6. LLM Investigation Brief (merchant_intelligence/brief.py)
  10. Self-improving harness (scripts/self_improve.py, use_aliases flag,
      shared golden set)

Run:  python tests/test_next_level.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import MerchantSearch, config
from merchant_intelligence.brief import (build_brief, build_template_brief,
                                         llm_available)
from merchant_intelligence.golden import (GOLDEN, golden_affinity, is_correct,
                                          scored_entries)

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


# ── 6. LLM Investigation Brief ────────────────────────────────────────────
print("\n[6] Investigation brief (template mode — no LLM key configured)")
# Golden set shared module
check("golden.py has scored entries (ground truth)",
      len(scored_entries()) >= 25, f"{len(scored_entries())}")
check("golden_affinity importable + works", callable(golden_affinity))
check("is_correct importable + callable", callable(is_correct))
check("GOLDEN has no duplicated queries",
      len(GOLDEN) == len({g['query'] for g in GOLDEN}))
check("GOLDEN entries carry emails or names (scored)",
      all(e.get('emails') or e.get('names') for e in scored_entries()))

# Template brief on a profile-shaped dict
sample_profile = {
    "query": "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
    "found": True,
    "family_count": 6,
    "seed": {"merchant_name": "SWEB_MARYLAND MALL", "match_type": "Alias Match",
             "overall_score": 100.0},
    "identity": {
        "email": {"label": "Emails", "total": 1,
                  "values": [{"value": "merchant@example.com", "count": 6}]},
        "phone": {"label": "Phones", "total": 0, "values": []},
    },
    "name_variants": [{"name": "SWEB_MARYLAND MALL", "count": 5},
                      {"name": "MARYLAND MALL", "count": 1}],
    "sources": [{"sheet": "2ISW Deployment Status", "count": 6}],
    "members": [{"id": 1, "email": "merchant@example.com",
                 "merchant_name": "SWEB_MARYLAND MALL"}],
    "alias_candidates": ["SWEB_MARYLAND MALL"],
}
tb = build_template_brief(sample_profile)
check("template brief mentions the seed name",
      "SWEB_MARYLAND MALL" in tb, tb[:120])
check("template brief mentions the confirmed email",
      "merchant@example.com" in tb)
check("template brief mentions name variants", "variants" in tb.lower())
check("template brief mentions sources", "source" in tb.lower())
check("template brief mentions alias candidates",
      "alias" in tb.lower())

# Not-found profile
nf = build_template_brief({"query": "ZZZ NOTHING", "found": False})
check("not-found brief is helpful", "ZZZ NOTHING" in nf and "No records" in nf)

# build_brief wrapper
out = build_brief(sample_profile)
check("build_brief returns brief text", bool(out["brief"]))
check("build_brief mode is template when no LLM",
      out["mode"] == "template", out["mode"])
check("build_brief found flag", out["found"] is True)
check("llm_available is False without key", llm_available() is False)
check("build_brief reports elapsed_ms", out["elapsed_ms"] >= 0)

# ── 9. Decisive-match family guard ────────────────────────────────────────
print("\n[9] Decisive-match family guard (no lookalike families)")
from merchant_intelligence.profile import MerchantProfile  # noqa: E402
from merchant_intelligence.entity import same_merchant_family  # noqa: E402
from merchant_intelligence import settings as engine_settings  # noqa: E402

check("DECISIVE_MATCH_THRESHOLD configured (85 = 8.5/10)",
      config.DECISIVE_MATCH_THRESHOLD == 85,
      f"{config.DECISIVE_MATCH_THRESHOLD}")
check("settings threshold resolves to the default",
      engine_settings.decisive_match_threshold() == 85.0,
      f"{engine_settings.decisive_match_threshold()}")
_all = engine_settings.all_settings()
check("all_settings exposes value + default (UI contract)",
      _all["decisive_match_threshold"]["value"] == 85.0
      and _all["decisive_match_threshold"]["default"] == 85,
      repr(_all["decisive_match_threshold"]))

# Tunable threshold: env var wins, settings file wins, range is enforced
_old_env = os.environ.get("DECISIVE_MATCH_THRESHOLD")
os.environ["DECISIVE_MATCH_THRESHOLD"] = "90"
try:
    check("env var overrides the threshold",
          engine_settings.decisive_match_threshold() == 90.0,
          f"{engine_settings.decisive_match_threshold()}")
finally:
    if _old_env is None:
        os.environ.pop("DECISIVE_MATCH_THRESHOLD", None)
    else:
        os.environ["DECISIVE_MATCH_THRESHOLD"] = _old_env

with tempfile.TemporaryDirectory() as _tmp:
    _old_file = os.environ.get("ENGINE_SETTINGS_FILE")
    os.environ["ENGINE_SETTINGS_FILE"] = str(Path(_tmp) / "engine_settings.json")
    try:
        engine_settings.save({"decisive_match_threshold": 92})
        check("settings file overrides the threshold",
              engine_settings.decisive_match_threshold() == 92.0,
              f"{engine_settings.decisive_match_threshold()}")
        engine_settings.save({"decisive_match_threshold": 500})
        check("out-of-range file value falls back to default",
              engine_settings.decisive_match_threshold() == 85.0,
              f"{engine_settings.decisive_match_threshold()}")
        _src = engine_settings.all_settings()
        check("settings source reported as file",
              _src["decisive_match_threshold"]["source"] != "built-in default",
              _src["decisive_match_threshold"]["source"])
    finally:
        if _old_file is None:
            os.environ.pop("ENGINE_SETTINGS_FILE", None)
        else:
            os.environ["ENGINE_SETTINGS_FILE"] = _old_file

# same_merchant_family: shared distinctive name token, or shared primary id
check("same-family by shared name token",
      same_merchant_family({"merchant_name": "TINA VENTURE", "tid": ""},
                           {"merchant_name": "Tina Oki", "tid": "2ISWN728"}))
check("same-family by shared TID",
      same_merchant_family({"merchant_name": "MEDPLUS PHARMACY",
                            "tid": "2ISW111A"},
                           {"merchant_name": "MEDPLUS LIMITED",
                            "tid": "2ISW111A"}))
check("different merchants rejected",
      not same_merchant_family({"merchant_name": "OKIEMUTE EKOKIFO",
                                "tid": "2ISWX336", "mxcode": "MX100376"},
                               {"merchant_name": "Tina Oki",
                                "tid": "2ISWN728", "mxcode": "MX85732"}))

# Live-registry behaviour: searching "OKI TINA" wins decisively (Tina Oki
# ~91.7) but the top-5 seeds ALSO include unrelated lookalikes (EMOKINIOVO
# OMOWHO, OKIEMUTE EKOKIFO). The guard must keep ONLY the winner's family.
# Pin the threshold to the default so a locally-tuned settings file cannot
# change the assertion (the tunable is covered by its own tests above).
_old_thr = os.environ.get("DECISIVE_MATCH_THRESHOLD")
os.environ["DECISIVE_MATCH_THRESHOLD"] = str(config.DECISIVE_MATCH_THRESHOLD)
try:
    _p = MerchantProfile()
    _oki = _p.build("OKI TINA")
    _oki_names = [str(m.get("merchant_name") or "") for m in _oki.get("members", [])]
    check("OKI TINA family excludes OKIEMUTE EKOKIFO",
          not any("EKOKIFO" in n.upper() for n in _oki_names),
          str(_oki_names))
    check("OKI TINA family excludes EMOKINIOVO OMOWHO",
          not any("EMOKINIOVO" in n.upper() for n in _oki_names),
          str(_oki_names))
    check("OKI TINA family still keeps the real merchant rows",
          any("TINA" in n.upper() for n in _oki_names),
          str(_oki_names))

    # MEDPLUS has MANY legitimate entries (MEDPLUS LIMITED + MEDPLUS PHARMACY
    # rows across sheets) — the guard must NOT collapse them.
    _med = _p.build("MEDPLUS")
    _med_names = {str(m.get("merchant_name") or "").upper()
                  for m in _med.get("members", [])}
    check("MEDPLUS family keeps multiple entries",
          len(_med.get("members", [])) >= 10,
          f"{len(_med.get('members', []))} rows")
    check("MEDPLUS family has both name variants",
          "MEDPLUS LIMITED" in _med_names and "MEDPLUS PHARMACY" in _med_names,
          str(sorted(_med_names)))

    # Identifier-fragment families must NOT be emptied by the cross-merchant
    # guard: a phone carried by LAGOON WATERS LTD rows AND by an
    # "Interswitch Limited/NNPC" row (the SAME terminal rows named
    # differently by two files — identical tid+mxcode+MID+account tuples).
    # The full-signature test must let them link, so the phone family is
    # populated again. The phone is pulled from the LOCAL database at
    # runtime — the repo never hardcodes merchant contact data.
    import sqlite3 as _sqlite3
    from merchant_intelligence import config as _cfg
    _conn = _sqlite3.connect(str(_cfg.active_db()))
    _row = _conn.execute(
        "SELECT phone FROM merchants WHERE merchant_name LIKE "
        "'%LAGOON WATERS%' AND phone LIKE '080%' AND length(phone)=11 "
        "LIMIT 1").fetchone()
    _conn.close()
    _ph_phone = (_row[0] if _row else "")
    if not _ph_phone:
        print("  ! no LAGOON WATERS 080-phone in local DB — skipping family check")
    else:
        _ph = _p.build(_ph_phone)
        _ph_names = {str(m.get("merchant_name") or "").upper()
                     for m in _ph.get("members", [])}
        check("phone fragment family is populated (not 0)",
              len(_ph.get("members", [])) >= 10,
              f"{len(_ph.get('members', []))} rows")
        check("phone family includes LAGOON WATERS",
              any("LAGOON WATERS" in n for n in _ph_names),
              str(sorted(_ph_names)))
        check("phone family includes the same merchant's other name",
              any("NNPC" in n for n in _ph_names),
              str(sorted(_ph_names)))

    # The JUST CHIPS fan-out the guard was BUILT to stop must stay blocked:
    # JUST CHIPS and OLAWALE ODUOLA share MX154553/email/phone but have
    # DIFFERENT MIDs and accounts — no shared full signature, no merge.
    _jc = _p.build("JUST CHIPS")
    _jc_names = {str(m.get("merchant_name") or "").upper()
                 for m in _jc.get("members", [])}
    check("JUST CHIPS family does NOT include OLAWALE ODUOLA",
          not any("OLAWALE" in n for n in _jc_names),
          str(sorted(_jc_names)))
    check("JUST CHIPS family keeps its own rows",
          any("JUST CHIPS" in n for n in _jc_names),
          str(sorted(_jc_names)))
finally:
    if _old_thr is None:
        os.environ.pop("DECISIVE_MATCH_THRESHOLD", None)
    else:
        os.environ["DECISIVE_MATCH_THRESHOLD"] = _old_thr

# ── 10. Self-improving harness ────────────────────────────────────────────
print("\n[10] Self-improving harness (alias-free mode + suggestions)")
# use_aliases=False must exist on both classes
s = MerchantSearch(use_aliases=False)
check("MerchantSearch(use_aliases=False) constructs", s is not None)
check("matcher.use_aliases flag propagated",
      s.matcher.use_aliases is False)
s2 = MerchantSearch()
check("default use_aliases=True", s2.matcher.use_aliases is True)

# Alias-free run against the live registry (small subset for speed)
from scripts import self_improve  # noqa: E402
rows = self_improve.run_alias_free(top=6)
check("harness ran the golden set", len(rows) >= 25, f"{len(rows)}")
check("each row has rank/score keys",
      all("rank" in r and "score" in r and "query" in r for r in rows))

agg = self_improve.aggregate(rows)
check("aggregate has recall1 in 0..1", 0.0 <= agg["recall1"] <= 1.0,
      f"{agg['recall1']}")
check("aggregate n matches rows", agg["n"] == len(rows))

# suggestions API (do NOT persist to the real alias cache — test on a
# temp ALIAS_CACHE_FILE so the real review queue is untouched)
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp) / "aliases.json"
    old = config.ALIAS_CACHE_FILE
    config.ALIAS_CACHE_FILE = tmp_path
    try:
        suggested = self_improve.suggest_aliases(rows)
        check("suggest_aliases returns a list", isinstance(suggested, list))
        check("suggestions capped per merchant",
              all(len([x for x in suggested if x['alias'] == r['query']]) <=
                  self_improve.MAX_SUGGESTIONS_PER_MERCHANT for r in rows))
    finally:
        config.ALIAS_CACHE_FILE = old

# Baseline load/save round-trip
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp) / "baseline.json"
    old = self_improve.BASELINE_FILE
    self_improve.BASELINE_FILE = tmp_path
    try:
        self_improve.save_baseline(agg, 6)
        loaded = self_improve.load_baseline()
        check("baseline saved + loaded", loaded.get("recall1") == agg["recall1"])
    finally:
        self_improve.BASELINE_FILE = old

# Reference-set sanity: recall@1 with aliases ON is still ~100% (benchmark)
sb = MerchantSearch()
top1 = 0
for entry in scored_entries():
    res = sb.search(entry["query"], limit=1, min_score=0)
    if res and is_correct(res[0], entry.get("emails", []), entry.get("names", [])):
        top1 += 1
check(f"aliased benchmark recall@1 high ({top1}/{len(scored_entries())})",
      top1 >= len(scored_entries()) - 2, f"{top1}/{len(scored_entries())}")

# ── 11. Build-time enrichment: quality scores + terminal timeline ────────
print("\n[11] Enrichment: quality scores + terminal timeline (merchant_events)")
from merchant_intelligence.enrich import (enrich_database, keys_for_query,  # noqa: E402
                                          timeline_for)

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "enrich.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE merchants (
            id INTEGER PRIMARY KEY,
            sheet_name TEXT, row_number INTEGER, merchant_name TEXT, tid TEXT,
            mxcode TEXT, merchant_id TEXT, account_number TEXT, email TEXT,
            phone TEXT, address TEXT, onboarded_date TEXT, raw_data TEXT);
    """)
    rows = [
        # 1 complete record -> 100
        (1, "File A :: Sheet1", 1, "COMPLETE MERCHANT LTD", "2ISW1001",
         "MX1001", "2ISW1", "1234567890", "a@x.com", "08011111111",
         "1 Main Rd Lagos", "2021-03-01", "{}"),
        # 2 barren record -> 100 - 20 - 20 - 15 - 10 = 35
        (2, "File A :: Sheet1", 2, "BARREN MERCHANT", "", "", "", "", "",
         "", "", "", "{}"),
        # 3/4 same TID, different names + different signatures -> conflict
        (3, "File A :: Sheet1", 3, "CONFLICT ONE", "2ISW2002", "MX2002",
         "", "2000000002", "b@x.com", "08022222222", "Addr 2",
         "2021-04-01", "{}"),
        (4, "File B :: Sheet2", 1, "CONFLICT TWO", "2ISW2002", "MX9009",
         "", "2000000009", "b@x.com", "08022222222", "Addr 9",
         "2021-05-01", "{}"),
        # 5/6 same merchant under two names, IDENTICAL full tuple -> benign
        (5, "File A :: Sheet1", 4, "LAGOON WATERS LTD", "2ISW3003",
         "MX3003", "2ISW3003MID", "3000000003", "c@x.com", "08033333333",
         "Addr 3", "2021-06-01", "{}"),
        (6, "File B :: Sheet2", 2, "INTERSWITCH LIMITED/NNPC 15",
         "2ISW3003", "MX3003", "2ISW3003MID", "3000000003", "c@x.com",
         "08033333333", "Addr 3", "2021-07-01", "{}"),
        # 7 change-of-details row with OLD/NEW account in raw_data
        (7, "2ISW_Parameter File 5 :: Change of merchant details", 9,
         "CHANGED MERCHANT LTD", "2ISW4004", "MX4004", "2ISW4",
         "4000000004", "d@x.com", "08044444444", "Addr 4", "2021-08-01",
         json.dumps({"OLD BANK ACC NO": "1111111111",
                     "NEW BANK ACC NO": "2222222222",
                     "OLD BANK CODE": "057", "NEW BANK CODE": "058",
                     "MONTH OF REQUEST": "2022-09-15"})),
    ]
    conn.executemany(
        "INSERT INTO merchants (id, sheet_name, row_number, merchant_name, "
        "tid, mxcode, merchant_id, account_number, email, phone, address, "
        "onboarded_date, raw_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()

    res = enrich_database(conn)
    check("enrich_database returned both counters",
          res["quality_rows"] == 7 and res["events"] > 0, str(res))

    def qscore(rid):
        return conn.execute(
            "SELECT quality_score, quality_flags FROM merchants WHERE id=?",
            (rid,)).fetchone()

    s1, f1 = qscore(1)
    check("complete record scores 100", s1 == 100, f"{s1} {f1}")
    s2, f2 = qscore(2)
    check("barren record scores 35 (all four misses)", s2 == 35, f"{s2}")
    check("barren record flags the four missing fields",
          set(json.loads(f2)) == {"missing_email", "missing_phone",
                                  "missing_account", "missing_address"}, f2)
    s3, f3 = qscore(3)
    check("conflicting name rows carry name_conflict",
          "name_conflict" in json.loads(f3), f3)
    check("conflicting rows share phone -> shared_identifier",
          "shared_identifier" in json.loads(f3), f3)
    s5, f5 = qscore(5)
    check("same merchant under two names is NOT a conflict",
          "name_conflict" not in json.loads(f5), f5)
    check("same-merchant tuple rows still score 100", s5 == 100, f"{s5}")

    # merchant_events timeline
    ev_types = {e[0] for e in conn.execute(
        "SELECT DISTINCT event_type FROM merchant_events")}
    check("events include first_seen", "first_seen" in ev_types, str(ev_types))
    check("events include name_variant", "name_variant" in ev_types,
          str(ev_types))
    check("events include account_change", "account_change" in ev_types,
          str(ev_types))

    tl = timeline_for(conn, "2ISW4004")
    changes = [e for e in tl if e["type"] == "account_change"]
    check("account_change event parsed old->new from raw_data",
          any("1111111111" in e["value"] and "2222222222" in e["value"]
              for e in changes), str(changes))
    check("account_change carries the request date",
          any(e.get("occurred_at") == "2022-09-15" for e in changes),
          str([e.get("occurred_at") for e in changes]))
    names = [e for e in tl if e["type"] == "name_variant"]
    check("name_variant recorded for the terminal",
          any("CHANGED MERCHANT" in e["value"] for e in names), str(names))
    first = [e for e in tl if e["type"] == "first_seen"]
    check("first_seen uses the onboarded date",
          any(e["value"] == "2021-08-01" for e in first), str(first))

    # keys_for_query resolves fragments to the terminal keys the registry
    # actually stores (DB-grounded resolution).
    ks = keys_for_query(conn, "2ISW4004")
    check("keys_for_query resolves a tid fragment",
          ("tid", "2ISW4004") in ks, str(ks))
    ks2 = keys_for_query(conn, "LAGOON WATERS")
    check("keys_for_query resolves via merchant name",
          ("tid", "2ISW3003") in ks2, str(ks2))
    # A name match must surface ALL identity keys of the matched rows — a
    # merchant whose rows carry only MX/MID/account (no TID) still gets a
    # timeline key. Row 5 carries MX3003 + 2ISW3003MID + account.
    ks3 = keys_for_query(conn, "INTERSWITCH")
    check("name match resolves MX-only keys too",
          ("mxcode", "MX3003") in ks3
          and ("merchant_id", "2ISW3003MID") in ks3, str(ks3))
    check("name match resolves account keys",
          ("account_number", "3000000003") in ks3, str(ks3))
    conn.close()

# Live-registry spot check: the built intelligence.db carries the new
# columns + events (guards against a stale build being queried — requires
# `python app.start rebuild` to have run with the enrichment step).
import sqlite3 as _sq
_live = _sq.connect(str(config.active_db()))
_cols = {r[1] for r in _live.execute("PRAGMA table_info(merchants)")}
check("live DB has quality_score column", "quality_score" in _cols, str(sorted(_cols)))
check("live DB has merchant_events table",
      _live.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name='merchant_events'").fetchone()[0] == 1)
_ev_n = _live.execute("SELECT COUNT(*) FROM merchant_events").fetchone()[0]
check("live DB has timeline events built", _ev_n > 0, f"{_ev_n}")
_live.close()

print("\n" + "=" * 60)
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
