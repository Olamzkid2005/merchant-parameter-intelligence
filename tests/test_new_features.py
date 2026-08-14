"""
Smoke test for the new Merchant Intelligence features:
  - search pagination (offset)
  - /api/search/export
  - /api/suggest (did-you-mean)
  - /api/similar (related merchants)
  - /api/duplicates (cluster detection)
  - /api/aliases (+ approve / reject review queue)
  - /api/report + /api/report/export (Phase 9 multi-sheet report)

Calls the FastAPI endpoint functions directly (no TestClient / httpx
dependency). Run:  python test_new_features.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api as api_mod

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {label} {extra}")
    else:
        FAIL += 1
        print(f"  [X]  {label} {extra}")


def main():
    print("== health ==")
    check("health", api_mod.health()["status"] == "ok")

    print("\n== search pagination ==")
    d1 = api_mod.search(api_mod.SearchRequest(query="LAGOON", limit=5, offset=0))
    n1 = d1.get("total", 0)
    check("search returns total", "total" in d1 and n1 >= 0, f"(total={n1})")
    check("search count==5", d1.get("count", 0) == 5, f"(count={d1.get('count')})")

    d2 = api_mod.search(api_mod.SearchRequest(query="LAGOON", limit=5, offset=5))
    check("search offset=5 ok", 0 <= d2.get("count", 0) <= 5,
          f"(count={d2.get('count')})")
    if d1.get("results") and d2.get("results"):
        ids1 = [x["id"] for x in d1["results"]]
        ids2 = [x["id"] for x in d2["results"]]
        check("offset pages differ", ids1 != ids2)

    print("\n== search export ==")
    r = api_mod.search_export(api_mod.SearchRequest(query="LAGOON", limit=5))
    check("export returns xlsx bytes", len(r.body) > 1000)
    ct = r.headers.get("content-type", "")
    check("export is xlsx", ct.startswith("application/vnd.openxmlformats"))
    check("export filename", "search_results" in r.headers.get("content-disposition", ""))

    print("\n== suggest (did-you-mean) ==")
    sug = api_mod.suggest(api_mod.SearchRequest(query="BIDGBENGA NIG LTD", limit=5))
    sugg_list = sug.get("suggestions", [])
    print(f"       suggestions for BIDGBENGA: {[s['query'] for s in sugg_list]}")
    check("suggest 200-style response has query", sug.get("query") == "BIDGBENGA NIG LTD")

    sug2 = api_mod.suggest(api_mod.SearchRequest(query="LAGOON WATERS"))
    check("suggest returns [] for good query", sug2.get("suggestions") == [])

    print("\n== similar (related merchants) ==")
    sim = api_mod.similar(api_mod.SearchRequest(query="LAGOON WATERS LTD", limit=10))
    print(f"       similar count: {sim.get('count')}")
    check("similar has list", "similar" in sim)
    if sim.get("similar"):
        check("similar members have name", bool(sim["similar"][0].get("merchant_name")))

    print("\n== duplicates ==")
    dup = api_mod.duplicates(limit=100)
    check("duplicates has clusters", "clusters" in dup, f"(count={dup.get('count')})")
    if dup.get("clusters"):
        c0 = dup["clusters"][0]
        check("cluster has occurrences>1", c0.get("occurrences", 0) > 1,
              f"({c0.get('merchant_name')}: {c0.get('occurrences')})")
        check("cluster has records", len(c0.get("records", [])) > 0)

    print("\n== aliases review queue ==")
    al = api_mod.aliases()
    check("aliases has counts", "counts" in al, f"(counts={al.get('counts')})")
    check("aliases has learned list", isinstance(al.get("learned"), list))

    # Approve + reject round-trip on a fresh alias
    test_alias = "ZZ SMOKE TEST ALIAS"
    test_canon = "LAGOON WATERS LTD"
    ar = api_mod.alias_approve(api_mod.AliasAction(alias=test_alias, canonical=test_canon))
    check("approve ok", ar.get("ok") is True)
    al = api_mod.aliases()
    found = any(i["alias"] == test_alias and i["status"] == "approved"
                for i in al.get("learned", []))
    check("approved alias visible", found)
    rr = api_mod.alias_reject(api_mod.AliasAction(alias=test_alias, canonical=test_canon))
    check("reject ok", rr.get("ok") is True)
    al = api_mod.aliases()
    gone = all(i["alias"] != test_alias for i in al.get("learned", []))
    check("rejected alias gone", gone)

    print("\n== report builder (Phase 9) ==")
    merchants = ["LAGOON WATERS LTD", "THE FILM HOUSE LIMITED", "ZZ FAKE CORP 12345"]
    rep = api_mod.report(api_mod.BatchRequest(merchants=merchants))
    print(f"       sheet_counts: {rep.get('sheet_counts')}")
    check("report has summary", len(rep.get("summary", [])) > 0)
    sc = rep.get("sheet_counts", {})
    check("report not_found>=1", sc.get("not_found", 0) >= 1,
          f"(not_found={sc.get('not_found')})")

    rex = api_mod.report_export(api_mod.BatchRequest(merchants=merchants))
    check("report export xlsx bytes", len(rex.body) > 1000)
    check("report export filename", "Merchant_Intelligence_Report" in
          rex.headers.get("content-disposition", ""))

    print(f"\n== RESULT: {PASS} passed, {FAIL} failed ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
