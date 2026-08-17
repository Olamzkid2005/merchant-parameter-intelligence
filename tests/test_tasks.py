"""
test_tasks.py — Tests for the natural-language task engine
(merchant_intelligence/tasks.py) + the /api/task endpoints.

Run:  python tests/test_tasks.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import tasks
from merchant_intelligence.tasks import (
    analyze, detect_intent, detect_intents, detect_task, execute_task,
    extract_compare_pair, extract_names, extract_params, extract_segment,
    looks_like_address, parse_identifiers, parse_named_identifiers,
    suggest_next_steps,
)

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


# ── Identifier parsing ────────────────────────────────────────────────────
print("\n[1] parse_identifiers")
text = ("2103O338\tFELIX OKONMAH\n2103O340\tADEBOWALE FESOMADE\n"
        "Pls get this merchant MXCODE, then use the mxcode to get the "
        "above merchant static account and the beneficiary name from "
        "static acct manager")
ids = parse_identifiers(text)
check("parses 2 TIDs", ids["tid"] == ["2103O338", "2103O340"], repr(ids["tid"]))
check("no MX in that text", ids["mxcode"] == [], repr(ids["mxcode"]))
check("email empty", ids["email"] == [])
check("phone empty", ids["phone"] == [])

mixed = parse_identifiers("MX184380 and 08000000000 and 2103O338 "
                          "and a@b.com and MX184380 again")
check("mixed MX codes deduped", mixed["mxcode"] == ["MX184380"], repr(mixed["mxcode"]))
check("mixed phone", mixed["phone"] == ["08000000000"], repr(mixed["phone"]))
check("mixed tid", mixed["tid"] == ["2103O338"], repr(mixed["tid"]))
check("mixed email", mixed["email"] == ["a@b.com"], repr(mixed["email"]))

# ── Intent detection ──────────────────────────────────────────────────────
print("\n[2] detect_intent")
check("static account intent", detect_intent(text) == "static_account",
      detect_intent(text))
check("mxcode intent", detect_intent("get the mxcode for 2103O338") == "mxcode")
check("email intent", detect_intent("find emails for MX184380") == "email")
check("phone intent", detect_intent("give me the phone number") == "phone")
check("profile intent", detect_intent("show full profile") == "profile")
check("resolve fallback", detect_intent("hello there") == "resolve")

# ── Extended identifier parsing (feature #3, DB-rooted) ───────────────────
print("\n[1b] extended identifiers (DB-rooted classifier)")
# These values are all REAL registry values — the classifier must place them
# by DB membership, not by shape guessing.
ids2 = parse_identifiers("5180857349 2800158 22439069072 2ISW123IFIS0001 035023")
check("10-digit static acc -> static (DB says so)",
      "5180857349" in ids2.get("static", []), repr(ids2.get("static")))
check("7-digit = payable", "2800158" in ids2["payable"], repr(ids2["payable"]))
check("11-digit = bvn", "22439069072" in ids2["bvn"], repr(ids2["bvn"]))
check("2ISW... = mid", "2ISW123IFIS0001" in ids2["mid"], repr(ids2["mid"]))
check("6-digit = alias", "035023" in ids2["alias"], repr(ids2["alias"]))
# A 10-digit static account must NOT be misread as a phone/BVN
check("static acc not bvn", "22439069072" not in ids2.get("static", []))
# English words and known prefixes must never be misread as identifiers
ids3 = parse_identifiers("FELIX OKONMAH 2103O338 static account 2800158 zkP5u7JM9")
check("7-letter word OKONMAH not any identifier",
      not any("OKONMAH" in ids3[k] for k in ids3), repr(ids3["payable"]))
check("word 'account' not any identifier",
      not any("account" in ids3[k] for k in ids3))
# zkP5u7JM9 is a real DB payable value -> classified as payable
check("zkP5u7JM9 IS payable (real DB value)", "zkP5u7JM9" in ids3["payable"])
# An MX code that is ALSO a payable code lands in both (resolve tries all)
ids4 = parse_identifiers("MX44117")
check("MX44117 is mxcode", "MX44117" in ids4.get("mxcode", []), repr(ids4))
# Bare real MX still not a task (gate unchanged)
# ── Named identifiers (feature #7 validation input) ───────────────────────
print("\n[1c] parse_named_identifiers")
named = parse_named_identifiers("2103O338\tFELIX OKONMAH\n2103O340 ADEBOWALE FESOMADE")
check("extracts id+name pairs", len(named) == 2, repr(named))
check("first pair tid", named[0]["id"] == "2103O338" and named[0]["name"] == "FELIX OKONMAH",
      repr(named[0]))

# ── Compound intents (feature #4) ─────────────────────────────────────────
print("\n[2b] compound intents")
check("single intent list", detect_intents("get the mxcode for 2103O338") == ["mxcode"])
both = detect_intents("get the mx codes and emails for these merchants")
check("mxcode + email both detected", set(both) == {"mxcode", "email"}, repr(both))
check("resolve fallback", detect_intents("hello") == ["resolve"])

# ── Address intent (per-merchant address extraction) ─────────────────────
print("\n[2c] address intent")
check("address intent detected (plural)",
      detect_intent("get me all the addresses for 2ISW916B") == "address",
      detect_intent("get me all the addresses for 2ISW916B"))
check("address intent detected (singular)",
      detect_intent("give me the address of LAGOON WATERS") == "address",
      detect_intent("give me the address of LAGOON WATERS"))
check("location word maps to address",
      detect_intent("show me the location for MX184380") == "address",
      detect_intent("show me the location for MX184380"))
check("plain name with 'address' is NOT a task",
      detect_task("PALM GROVE ADDRESS") is None)
# The user's exact scenario: identifier + address request -> the address
# pipeline returns the real street address from the registry.
addr = detect_task("get me all the addresses for 2ISW916B")
check("address task detected", addr is not None and addr.get("intent") == "address",
      repr(addr and addr.get("intent")))
if addr:
    raddr = execute_task(addr)
    check("address pipeline ran", raddr.get("intent") == "address",
          raddr.get("intent"))
    check("address table has Address column", "Address" in raddr.get("columns", []),
          repr(raddr.get("columns")))
    if raddr["rows"]:
        check("address row has a real address", bool(raddr["rows"][0].get("Address")),
              repr(raddr["rows"][0].get("Address")))
# 'all the addresses of all NNPC' must route to SEGMENT only — the address
# intent is subsumed, never compounded with the segment pipeline.
addr_seg = detect_task("get me all the addresses of all nnpc stations")
check("segment request stays segment-only",
      addr_seg is not None and addr_seg.get("intent") == "segment"
      and addr_seg.get("intents") == ["segment"],
      repr((addr_seg or {}).get("intents")))
# Weak 'number of' count loses to the address field it refers to (same
# disambiguation rule as email/phone/mxcode).
check("weak count loses to address field",
      detect_intents("get the number of addresses for 2ISW916B") == ["address"],
      repr(detect_intents("get the number of addresses for 2ISW916B")))
# Address chains with other field intents in compound requests.
check("address + email compound",
      set(detect_intents("get the address and email for 2ISW916B")) == {"address", "email"},
      repr(detect_intents("get the address and email for 2ISW916B")))

# ── New field + analytical intents (the 17 added alongside address) ──────
print("\n[2d] new intents (field extraction + analytical)")
check("bank intent",
      detect_intent("get me the bank for 2ISW916B") == "bank",
      detect_intent("get me the bank for 2ISW916B"))
check("account name intent",
      detect_intent("what's the account name for MX141692") == "account_name",
      detect_intent("what's the account name for MX141692"))
check("account number intent",
      detect_intent("get the account number for 2ISW916B") == "account_number",
      detect_intent("get the account number for 2ISW916B"))
check("payable subsumed by static_account (its pipeline returns Payable Code)",
      detect_intents("get the payable code for MX141692") == ["static_account"],
      repr(detect_intents("get the payable code for MX141692")))
# static_account's pipeline already returns alias/payable/beneficiary columns,
# so the alias intent is only reachable when static_account is negated out.
_t_alias_det = detect_task("list the aliases for MX141692 but not the static account")
check("alias intent (via negation)",
      _t_alias_det is not None and _t_alias_det.get("intent") == "alias",
      repr((_t_alias_det or {}).get("intent")))
check("contact intent",
      detect_intent("who is the contact person at LAGOON WATERS") == "contact",
      detect_intent("who is the contact person at LAGOON WATERS"))
check("onboarded intent",
      detect_intent("when was 2ISW916B onboarded") == "onboarded",
      detect_intent("when was 2ISW916B onboarded"))
check("state intent",
      detect_intent("what state is 2ISW916B in") == "state",
      detect_intent("what state is 2ISW916B in"))
check("source intent",
      detect_intent("which file is MX141692 in") == "source",
      detect_intent("which file is MX141692 in"))
check("static_account subsumes payable + alias",
      detect_intents("get me all the alias and payables mapped to MX141692 "
                     "from static acct manager") == ["static_account"],
      repr(detect_intents("get me all the alias and payables mapped to MX141692 "
                          "from static acct manager")))
check("ranking drops weak count",
      detect_intents("how many merchants per state") == ["top"],
      repr(detect_intents("how many merchants per state")))
check("top-10 ranking drops its field",
      detect_intents("top 10 banks in the NNPC file") == ["top"],
      repr(detect_intents("top 10 banks in the NNPC file")))
check("compare detected",
      detect_intents("compare LAGOON WATERS vs ARTEE INDUSTRIES") == ["compare"],
      repr(detect_intents("compare LAGOON WATERS vs ARTEE INDUSTRIES")))
check("compare pair extracted",
      extract_compare_pair("compare LAGOON WATERS vs ARTEE INDUSTRIES")
      == ["LAGOON WATERS", "ARTEE INDUSTRIES"],
      repr(extract_compare_pair("compare LAGOON WATERS vs ARTEE INDUSTRIES")))
check("verify detected",
      detect_intent("is 2103O338 in the registry") == "verify",
      detect_intent("is 2103O338 in the registry"))
check("related detected",
      detect_intent("who else is linked to MX141692") == "related",
      detect_intent("who else is linked to MX141692"))
check("formerly detected",
      detect_intent("what was just chips formerly called") == "formerly",
      detect_intent("what was just chips formerly called"))
check("coverage detected (and subsumes its field)",
      detect_intents("which nnpc stations have no email") == ["coverage"],
      repr(detect_intents("which nnpc stations have no email")))
check("verb-less field request becomes a task",
      detect_task("what state is lagoons in") is not None)
check("weak field signal on a name stays a search",
      detect_task("BANK OF INDUSTRY") is None)

# ── Task detection (the gate) ─────────────────────────────────────────────
print("\n[3] detect_task")
check("multi-line paste w/ identifiers is a task",
      detect_task(text) is not None)
check("task intent is static_account",
      (detect_task(text) or {}).get("intent") == "static_account")
check("plain name is NOT a task",
      detect_task("LAGOON WATERS") is None)

# ── Per-TID static rows (MEDPLUS shares MX3490 across 1000+ terminals) ───
# The static pipeline must return each TID's OWN static-account-manager row
# (QTB source sheet), not the first MX-level row the merge collapses to.
_sa = execute_task(detect_task("get me the payable for "
                               "2ISWA842 2ISWK935 2ISW2571",
                               use_llm=False))
_sa_by_id = {str(r.get("identifier")).upper(): r for r in _sa["rows"]}
check("per-TID static: 2ISWA842 QTB payable",
      _sa_by_id.get("2ISWA842", {}).get("payable_code") == "1156555",
      repr(_sa_by_id.get("2ISWA842", {}).get("payable_code")))
check("per-TID static: 2ISWA842 alias + static acc",
      _sa_by_id.get("2ISWA842", {}).get("alias") == "022962"
      and _sa_by_id.get("2ISWA842", {}).get("static_acc_no") == "5180005449",
      repr((_sa_by_id.get("2ISWA842", {}).get("alias"),
            _sa_by_id.get("2ISWA842", {}).get("static_acc_no"))))
check("per-TID static: 2ISWK935 QTB payable",
      _sa_by_id.get("2ISWK935", {}).get("payable_code") == "1019218",
      repr(_sa_by_id.get("2ISWK935", {}).get("payable_code")))
check("per-TID static: 2ISW2571 QTB payable",
      _sa_by_id.get("2ISW2571", {}).get("payable_code") == "9306953",
      repr(_sa_by_id.get("2ISW2571", {}).get("payable_code")))
# Distinct QTB values per terminal, never one shared row for the whole MX.
check("per-TID static: no MX-collapse (3 distinct payables)",
      len({r.get("payable_code") for r in _sa["rows"]}) == 3,
      repr([r.get("payable_code") for r in _sa["rows"]]))
# Confusable look-alikes (Z↔2) must never steal an EXACT TID match:
# 2ISWZ321 and 2ISW2321 are both real registry TIDs.
_z = execute_task(detect_task("get me the payable for 2ISWZ321",
                               use_llm=False))
check("confusable: exact TID wins (2ISWZ321 not 2ISW2321)",
      any(str(r.get("tid")).upper() == "2ISWZ321" for r in _z["rows"])
      and not any(str(r.get("tid")).upper() == "2ISW2321"
                  for r in _z["rows"]),
      repr([(r.get("tid"), r.get("payable_code")) for r in _z["rows"]]))
check("confusable: exact TID keeps its own QTB row",
      any(str(r.get("payable_code")) == "8907390" for r in _z["rows"]),
      repr([r.get("payable_code") for r in _z["rows"]]))
# ── Alias/payable field pipelines also prefer the terminal's own QTB row ──
# The alias/payable INTENTS resolve via resolve_any (first registry row =
# the parameter file), so they must upgrade to the TID's own static-account-
# manager row the same way the static_account pipeline does (MEDPLUS:
# QTB alias 022962 vs parameter alias 006793 for 2ISWA842).
_a = execute_task(detect_task("get me the aliases for "
                               "2ISWA842 2ISWK935 2ISW2571",
                               use_llm=False, intent_override="alias"))
_a_by_id = {str(r.get("identifier")).upper(): r for r in _a["rows"]}
check("alias intent: QTB alias per terminal",
      _a_by_id.get("2ISWA842", {}).get("Alias") == "022962"
      and _a_by_id.get("2ISWK935", {}).get("Alias") == "022963"
      and _a_by_id.get("2ISW2571", {}).get("Alias") == "022964",
      repr([(r.get("identifier"), r.get("Alias")) for r in _a["rows"]]))
_p = execute_task(detect_task("get me the payable for "
                               "2ISWA842 2ISWK935 2ISW2571",
                               use_llm=False, intent_override="payable"))
_p_by_id = {str(r.get("identifier")).upper(): r for r in _p["rows"]}
check("payable intent: QTB payable per terminal",
      _p_by_id.get("2ISWA842", {}).get("Payable Code") == "1156555"
      and _p_by_id.get("2ISWK935", {}).get("Payable Code") == "1019218"
      and _p_by_id.get("2ISW2571", {}).get("Payable Code") == "9306953",
      repr([(r.get("identifier"), r.get("Payable Code")) for r in _p["rows"]]))
# Natural phrasing routes to static_account, which already returns the same
# per-TID QTB alias/payable values (so the task export's Alias column is
# correct without an override).
_nat = execute_task(detect_task("get me all the alias for these TIDs\n"
                                "2ISWA842 2ISWK935 2ISW2571",
                                use_llm=False))
_nat_by_id = {str(r.get("identifier")).upper(): r for r in _nat["rows"]}
check("alias phrasing: QTB alias via static_account path",
      _nat_by_id.get("2ISWA842", {}).get("alias") == "022962"
      and _nat_by_id.get("2ISWK935", {}).get("alias") == "022963",
      repr([(r.get("identifier"), r.get("alias")) for r in _nat["rows"]]))
# ── Name-based static requests prefer the QTB source ─────────────────────
# 'get the static account for LAGOON WATERS' must return the merchant's
# QTB static account (5180851086), not a parameter-file first row.
_ns1 = execute_task(detect_task("get the static account for LAGOON WATERS",
                                use_llm=False))
_ns1_by_id = {str(r.get("identifier")).upper(): r for r in _ns1["rows"]}
check("name static: LAGOON QTB static account",
      _ns1_by_id.get("LAGOON WATERS", {}).get("static_acc_no") == "5180851086",
      repr(_ns1_by_id.get("LAGOON WATERS", {}).get("static_acc_no")))
check("name static: LAGOON QTB payable/alias/bank present",
      bool(_ns1_by_id.get("LAGOON WATERS", {}).get("payable_code"))
      and bool(_ns1_by_id.get("LAGOON WATERS", {}).get("alias"))
      and bool(_ns1_by_id.get("LAGOON WATERS", {}).get("bank")),
      repr((_ns1_by_id.get("LAGOON WATERS", {}).get("payable_code"),
            _ns1_by_id.get("LAGOON WATERS", {}).get("alias"),
            _ns1_by_id.get("LAGOON WATERS", {}).get("bank"))))
# MEDPLUS name rows carry NO mxcode (standalone workbook) — the terminal's
# own QTB row (keyed by TID) must supply the static data, not a blank row.
_ns2 = execute_task(detect_task("get the static account for MEDPLUS",
                                use_llm=False))
_ns2_first = next((r for r in _ns2["rows"]
                   if str(r.get("identifier")).upper() == "MEDPLUS"), {})
check("name static: MEDPLUS row gets QTB static account (not blank)",
      bool(_ns2_first.get("static_acc_no"))
      and str(_ns2_first.get("static_acc_no")).startswith("5180"),
      repr(_ns2_first.get("static_acc_no")))
check("name static: MEDPLUS QTB beneficiary is Medplus Limited",
      str(_ns2_first.get("beneficiary")).upper() == "MEDPLUS LIMITED",
      repr(_ns2_first.get("beneficiary")))
check("single bare MX is NOT a task",
      detect_task("MX183544") is None)
check("single MX + instruction IS a task",
      detect_task("get the static account for MX183544") is not None)
check("two identifiers is a task",
      detect_task("2103O338\nMX184380") is not None)
check("multi-line names only is NOT a task",
      detect_task("LAGOON WATERS\nARTEE INDUSTRIES") is None)
check("empty is not a task", detect_task("") is None)

# Compound task executes both pipelines into one merged table (feature #4)
compound = detect_task("2103O338\nMX184380\nget the mx codes and emails for these")
res_compound = execute_task(compound)
check("compound task detected", compound is not None)
check("compound has both intents", set(compound.get("intents", [])) >= {"mxcode", "email"},
      repr(compound.get("intents")))
check("compound merged table has rows", len(res_compound["rows"]) >= 1,
      f"{len(res_compound['rows'])}")
if res_compound["rows"]:
    r0 = res_compound["rows"][0]
    check("compound row merged identifier", bool(r0.get("identifier")))
    check("compound has email column value or mx code",
          bool(r0.get("email") or r0.get("mxcode")),
          repr(r0))

# Per-row status + name validation (feature #7)
test_status = detect_task("2103O338\tWRONG NAME\nget static account")
res_status = execute_task(test_status)
check("name-mismatch task detected", test_status is not None)
if res_status["rows"]:
    st = res_status["rows"][0].get("status")
    check("name mismatch flagged on wrong name", st == "name_mismatch", repr(st))

test_status2 = detect_task("2103O338\tFELIX OKONMAH\nget static account")
res_status2 = execute_task(test_status2)
if res_status2["rows"]:
    st2 = res_status2["rows"][0].get("status")
    check("correct name is found", st2 in ("found", "name_mismatch"), repr(st2))

# Next-step suggestions (feature #10)
check("static-account run suggests other steps",
      len(res_status.get("suggestions", [])) >= 1, repr(res_status.get("suggestions")))
if res_status.get("suggestions"):
    s0 = res_status["suggestions"][0]
    check("suggestion has prompt", bool(s0.get("prompt")))
    check("suggestion has label", bool(s0.get("label")))

# ── Input-size guard + precompiled patterns (production hardening) ────────
print("\n[4e] input guard + precompiled patterns")
from merchant_intelligence.tasks import (
    COMPILED_INTENT_PATTERNS, MAX_INPUT_CHARS, _whole_word_re,
)

check("MAX_INPUT_CHARS is a sane bound", MAX_INPUT_CHARS == 50_000, str(MAX_INPUT_CHARS))
check("all intent patterns precompiled",
      all(isinstance(p, type(re.compile(""))) for pats in COMPILED_INTENT_PATTERNS.values()
          for p, _w in pats))
big = "M" * (MAX_INPUT_CHARS + 1)
try:
    detect_task(big)
    check("oversized paste raises ValueError", False)
except ValueError as exc:
    check("oversized paste raises ValueError", "too large" in str(exc).lower(), str(exc)[:80])
check("normal-size task unaffected by guard",
      detect_task("get me all the addresses of all nnpc stations") is not None)
# Whole-word cache: compiled once, reused (boundary semantics unchanged)
check("whole-word re matches 'all' alone", bool(_whole_word_re("all").search("get all the")))
check("whole-word re ignores 'ball'", not bool(_whole_word_re("all").search("basketball")))

# ── Typed contracts (dataclasses, feature: models.py) ─────────────────────
print("\n[4f] TaskDescriptor + PipelineResult dataclasses")
from merchant_intelligence.tasks import PipelineResult, TaskDescriptor

d = TaskDescriptor(
    intent="static_account",
    intents=["static_account"],
    identifiers={"tid": ["2103O338"]},
    named=[{"id": "2103O338", "name": "FELIX OKONMAH"}],
    names=[],
    identifier_count=1,
    has_instruction=True,
    multiline=True,
    confidence=92,
    params={"state": "LAGOS"},
    raw="2103O338 FELIX OKONMAH",
)
check("TaskDescriptor default intent is resolve",
      TaskDescriptor().intent == "resolve")
check("TaskDescriptor keeps identifiers",
      d.identifiers["tid"] == ["2103O338"])
check("TaskDescriptor keeps named pairs",
      d.named[0]["name"] == "FELIX OKONMAH")
check("TaskDescriptor round-trips through to_dict",
      TaskDescriptor.from_dict(d.to_dict()).intent == "static_account")
rt = TaskDescriptor.from_dict(d.to_dict())
check("round-trip preserves identifiers", rt.identifiers == d.identifiers)
check("round-trip preserves params", rt.params == {"state": "LAGOS"})
check("round-trip preserves raw text", rt.raw == d.raw)
check("from_dict tolerates missing keys",
      TaskDescriptor.from_dict({}).intent == "resolve")
check("default lists are independent (no shared mutable state)",
      TaskDescriptor().intents is not TaskDescriptor().intents)
# detect_task returns a dict whose shape matches TaskDescriptor.to_dict()
real = detect_task("get the static account for MX183544")
check("detect_task dict matches TaskDescriptor keys",
      set(real.keys()) == set(TaskDescriptor().to_dict().keys()),
      f"{sorted(real.keys())} vs {sorted(TaskDescriptor().to_dict().keys())}")

r = PipelineResult(
    intent="static_account",
    pipeline=["resolve_mx", "static_account"],
    summary="Resolved 1 of 1",
    columns=["TID", "Static Account Number"],
    rows=[{"TID": "2103O338", "Static Account Number": "5180857349"}],
    not_found=[{"value": "MX999999", "reason": "not in db"}],
)
check("PipelineResult keeps rows", r.rows[0]["TID"] == "2103O338")
check("PipelineResult round-trips through to_dict",
      PipelineResult.from_dict(r.to_dict()).summary == r.summary)
rt2 = PipelineResult.from_dict(r.to_dict())
check("PipelineResult round-trip preserves rows", rt2.rows == r.rows)
check("PipelineResult round-trip preserves not_found", rt2.not_found == r.not_found)
check("PipelineResult default has no suggestions", PipelineResult().suggestions == [])
# execute_task returns a dict whose shape matches PipelineResult.to_dict()
res_dict = execute_task(detect_task("get the static account for MX183544"))
check("execute_task dict matches PipelineResult keys",
      set(res_dict.keys()) == set(PipelineResult().to_dict().keys()),
      f"{sorted(res_dict.keys())} vs {sorted(PipelineResult().to_dict().keys())}")

# ── Clause-level entity extraction (attach each intent to its own id) ─────
print("\n[4g] clause-level entity extraction")
from merchant_intelligence.tasks import extract_clause_entities, split_clauses

cl = split_clauses("get email for 2103O338 and phone for MX141692")
check("split_clauses splits on 'and'",
      cl == ["get email for 2103O338", "phone for MX141692"], repr(cl))

ents = extract_clause_entities("get email for 2103O338 and phone for MX141692")
check("extracts two clause entities", len(ents) == 2, repr(ents))
by_intent = {e["intent"]: e["identifiers"] for e in ents}
check("email attached to 2103O338",
      by_intent.get("email", {}).get("tid") == ["2103O338"], repr(by_intent))
check("phone attached to MX141692",
      by_intent.get("phone", {}).get("mxcode") == ["MX141692"], repr(by_intent))

# Id-only trailing clause inherits the previous intent
ents2 = extract_clause_entities("get email for 2103O338 and 2103O340")
check("id-only clause inherits email intent",
      any(e["intent"] == "email" and set(e["identifiers"].get("tid", [])) ==
          {"2103O338", "2103O340"} for e in ents2), repr(ents2))

# <2 identifiers / name-only requests -> nothing to attach
check("single id -> no clauses",
      extract_clause_entities("get email for 2103O338") == [])
check("no ids -> no clauses",
      extract_clause_entities("RUBELS AND ANGELS RESTAURANT") == [])
check("split_clauses splits raw names (guard lives in the extractor)",
      split_clauses("RUBELS AND ANGELS RESTAURANT") ==
      ["RUBELS", "ANGELS RESTAURANT"],
      repr(split_clauses("RUBELS AND ANGELS RESTAURANT")))

# detect_task carries the clauses; execute_task scopes each pipeline
ct = detect_task("get email for 2103O338 and phone for MX141692")
check("clause compound detected as task", ct is not None)
check("task descriptor has clauses", bool(ct and ct.get("clauses")),
      repr((ct or {}).get("clauses")))
if ct:
    rc = execute_task(ct)
    ids = {r.get("identifier") for r in rc.get("rows", [])}
    check("clause-scoped rows cover both identifiers",
          ids == {"2103O338", "MX141692"}, repr(ids))
    cols = set(rc.get("columns", []))
    check("clause-scoped merged table has Email + Phone columns",
          {"Email", "Phone"} <= cols, repr(rc.get("columns")))

# Compound resolve paste (leading id-only) must NOT be scoped — full set kept
cres = detect_task("2103O338\nMX184380")
check("id-only paste has no clauses", (cres or {}).get("clauses") == [])
if cres:
    rres = execute_task(cres)
    check("resolve paste still covers both ids",
          {r.get("identifier") for r in rres.get("rows", [])} ==
          {"2103O338", "MX184380"}, repr(rres.get("rows")))

# A clause whose intent was dropped from the intents list (static_account
# subsumes mxcode) must NOT scope — the dropped clause's id stays in the
# result instead of vanishing from the output.
cvanish = detect_task("get mxcode for 2103O338 and static account for MX141692")
check("mx+static clause task detected", cvanish is not None)
if cvanish:
    rvanish = execute_task(cvanish)
    vid = {r.get("identifier") for r in rvanish.get("rows", [])}
    check("dropped-clause id still present (no data vanish)",
          "2103O338" in vid, repr(vid))

# Comma-separated trailing id inherits the clause intent — email covers both
ccom = detect_task("get email for 2103O338, 2103O340")
check("comma paste task detected", ccom is not None)
if ccom:
    rcom = execute_task(ccom)
    check("comma paste email covers both tids",
          {r.get("identifier") for r in rcom.get("rows", [])} ==
          {"2103O338", "2103O340"}, repr(rcom.get("rows")))

# ── Intents config file (tunable without code) ────────────────────────────
print("\n[4h] intents.json config")
import json as _json
import os as _os
from pathlib import Path as _Path

import merchant_intelligence.tasks.vocab as _vocab

_cfg_path = _Path(_vocab.__file__).resolve().parent / "intents.json"
check("intents.json exists", _cfg_path.exists(), str(_cfg_path))
_cfg_data = _json.loads(_vocab._strip_comment_lines(
    _cfg_path.read_text(encoding="utf-8")))
check("config is valid JSON + has intents",
      isinstance(_cfg_data.get("intents"), dict), repr(list(_cfg_data)[:5]))

# Every pattern in the config file compiles (a regex typo must never slip in)
_bad_regex = []
for _intent, _spec in (_cfg_data.get("intents") or {}).items():
    for _p in _spec.get("patterns", []):
        try:
            re.compile(_p["pattern"])
        except re.error as _e:
            _bad_regex.append((_intent, _p["pattern"], str(_e)))
check("all config patterns compile", not _bad_regex, repr(_bad_regex[:3]))

# Config file (as shipped) must stay in sync with the code defaults
check("config patterns match code defaults",
      _vocab.INTENT_PATTERNS == _vocab._DEFAULT_INTENT_PATTERNS,
      "config has drifted from defaults - rebuild the file")
check("config keywords match code defaults",
      _vocab.INTENT_KEYWORDS == _vocab._DEFAULT_INTENT_KEYWORDS,
      "keywords drifted")
check("every config intent has keywords",
      all(isinstance((s or {}).get("keywords"), list)
          for s in (_cfg_data.get("intents") or {}).values()))

# Whole-line comments are tolerated (// and #)
_stripped = _vocab._strip_comment_lines("{\n// comment\n# another\n\"a\": 1\n}")
check("comment lines stripped",
      _json.loads(_stripped) == {"a": 1}, repr(_stripped))

# env-var override: a custom file is loaded instead of the shipped one
_tmp_cfg = _Path(__file__).resolve().parent / "_probe_intents.json"
_tmp_cfg.write_text(
    _json.dumps({"intents": {"email": {"patterns": [
        {"pattern": "\\bEMAILS-FOR-ME\\b", "weight": 9}],
        "keywords": ["email"]}}}),
    encoding="utf-8")
_old_env = _os.environ.get("MERCHANT_INTENTS_CONFIG")
_os.environ["MERCHANT_INTENTS_CONFIG"] = str(_tmp_cfg)
try:
    import importlib as _importlib
    _importlib.reload(_vocab)
    check("env override loads custom file",
          "EMAILS-FOR-ME" in _vocab.INTENT_PATTERNS.get("email", [()])[0][0],
          repr(_vocab.INTENT_PATTERNS.get("email")))
    check("env override: other intents fall back to defaults",
          "phone" in _vocab.INTENT_PATTERNS)
finally:
    if _old_env is None:
        _os.environ.pop("MERCHANT_INTENTS_CONFIG", None)
    else:
        _os.environ["MERCHANT_INTENTS_CONFIG"] = _old_env
    _importlib.reload(_vocab)  # restore the shipped config
    _tmp_cfg.unlink(missing_ok=True)
check("shipped config restored after probe",
      _vocab.INTENT_PATTERNS == _vocab._DEFAULT_INTENT_PATTERNS)

# A typo'd config (invalid regex / non-numeric weight) must never crash the
# engine at import — bad entries are skipped, valid ones still load.
_tmp_bad = _Path(__file__).resolve().parent / "_probe_bad_intents.json"
_tmp_bad.write_text(_json.dumps({"intents": {
    "email": {"patterns": [
        {"pattern": "\\bEMAILS-FOR-ME\\b", "weight": 9},
        {"pattern": "[unclosed", "weight": 5},
        {"pattern": "\\bOK-ONE\\b", "weight": "high"},
    ], "keywords": ["email"]}}}))
_old_env2 = _os.environ.get("MERCHANT_INTENTS_CONFIG")
_os.environ["MERCHANT_INTENTS_CONFIG"] = str(_tmp_bad)
try:
    _importlib.reload(_vocab)
    _email_pats = _vocab.INTENT_PATTERNS.get("email", [])
    check("typo'd config still loads (no crash)",
          "EMAILS-FOR-ME" in _email_pats[0][0], repr(_email_pats))
    check("invalid regex pattern skipped",
          all("[unclosed" not in p for p, _w in _email_pats), repr(_email_pats))
    check("bad weight falls back to 1",
          any(p == "\\bOK-ONE\\b" and w == 1 for p, w in _email_pats),
          repr(_email_pats))
    check("compiled patterns built without error",
          all(hasattr(p, "search") for pats in _vocab.COMPILED_INTENT_PATTERNS.values()
              for p, _w in pats))
finally:
    if _old_env2 is None:
        _os.environ.pop("MERCHANT_INTENTS_CONFIG", None)
    else:
        _os.environ["MERCHANT_INTENTS_CONFIG"] = _old_env2
    _importlib.reload(_vocab)
    _tmp_bad.unlink(missing_ok=True)
check("shipped config restored after typo probe",
      _vocab.INTENT_PATTERNS == _vocab._DEFAULT_INTENT_PATTERNS)

# ── API smoke (endpoints) ────────────────────────────────────────────────

# ── NLU upgrades: negation + workflow planner + offline semantic fallback ──
print("\n[4j] negation + workflow planner + semantic fallback")
from merchant_intelligence.tasks import analyze as _analyze_pub
from merchant_intelligence.tasks.intents import (
    _analyze, _detect_negated_intents, _phrase_similarity, _stem,
    build_execution_plan,
)

# Negation: '...but not the change history' excludes change_details.
_neg1 = _detect_negated_intents(
    "get account details for 2103O338 but not the change history")
check("negation: change_history detected as excluded",
      "change_details" in _neg1, repr(_neg1))
_tn = detect_task("get account details for 2103O338 but not the change history")
check("negation: excluded lands on descriptor",
      _tn is not None and _tn.get("excluded") == ["change_details"],
      repr(_tn and _tn.get("excluded")))
check("negation: excluded intent never runs",
      _tn is not None and "change_details" not in _tn.get("intents", []),
      repr(_tn and _tn.get("intents")))
check("negation: request still routes (profile)",
      _tn is not None and _tn.get("intent") == "profile",
      repr(_tn and _tn.get("intent")))

# Compound + negation: 'emails but not the phones' keeps only email.
_tn2 = detect_task("give me the emails but not the phones for MX141692")
check("negation: phones excluded in compound",
      _tn2 is not None and _tn2.get("excluded") == ["phone"]
      and _tn2.get("intents") == ["email"],
      repr(_tn2 and (_tn2.get("excluded"), _tn2.get("intents"))))

# Presence filters are NOT negation: 'stations without email' excludes nothing.
check("presence filter is not negation",
      _detect_negated_intents("show me the stations without email") == [])

# Nearest-intent-only: a later POSITIVE clause is never swept into the
# exclusion ("...but not the change history, and also the email" keeps email).
_neg2 = _detect_negated_intents(
    "get account details for 2103O338 but not the change history, and also the email")
check("negation: later positive clause not swept in",
      _neg2 == ["change_details"], repr(_neg2))
_tn3 = detect_task(
    "get account details for 2103O338 but not the change history, and also the email")
check("negation: email survives compound-with-negation",
      _tn3 is not None and "email" in (_tn3.get("intents") or [])
      and _tn3.get("excluded") == ["change_details"],
      repr(_tn3 and (_tn3.get("intents"), _tn3.get("excluded"))))

# 'not a'/'not an' negate only the word right after the article, so
# "not available in the email file" never excludes email, but "not an email"
# (e.g. "this is not an email, it is a tid") does.
check("negation: 'not available' is not an exclusion",
      _detect_negated_intents("not available in the email file") == [],
      repr(_detect_negated_intents("not available in the email file")))
check("negation: 'not an email' excludes email",
      _detect_negated_intents("this is not an email, it is a tid") == ["email"],
      repr(_detect_negated_intents("this is not an email, it is a tid")))

# Multiple distinct negations in one request: each marker excludes its own
# nearest intent.
_neg3 = _detect_negated_intents(
    "get the emails but not the phones, and not the change history")
check("negation: two markers exclude two intents",
      set(_neg3) == {"phone", "change_details"}, repr(_neg3))
_tn4 = detect_task("get the emails but not the phones, and not the change history")
check("negation: multi-negation descriptor",
      _tn4 is not None and set(_tn4.get("excluded", [])) == {"phone", "change_details"}
      and _tn4.get("intents") == ["email"],
      repr(_tn4 and (_tn4.get("intents"), _tn4.get("excluded"))))

# Word-boundary scan: a short keyword must never misfire inside a longer
# word — 'count' inside 'account' must not exclude the count intent.
check("negation: 'count' does not match inside 'account'",
      _detect_negated_intents("but not the account") == [],
      repr(_detect_negated_intents("but not the account")))

# Workflow planner (intent graph)
_plan = build_execution_plan(["static_account"])
check("plan workflow for static_account",
      _plan["workflow"] == ["fetch_static_account"], repr(_plan))
check("plan lists internal requirement",
      _plan["steps"][0]["resolved_internally"] == ["mxcode"]
      and _plan["steps"][0]["requires"] == ["resolve_mxcode"],
      repr(_plan["steps"]))
_t3 = detect_task("get email for 2103O338 and phone for MX141692")
check("compound task exposes workflow",
      _t3 is not None
      and _t3.get("workflow", {}).get("workflow")
      == ["fetch_email", "fetch_phone"],
      repr(_t3 and _t3.get("workflow")))

# Offline semantic fallback: plural 'static accounts' misses every regex but
# classifies via the 'static account' keyword (token coverage, plural-tolerant).
check("phrase coverage high for plural",
      _phrase_similarity("pull up the static accounts",
                        ["static account", "static acct"])[0] >= 0.75,
      str(_phrase_similarity("pull up the static accounts",
                             ["static account", "static acct"])))
check("plural static accounts -> static_account",
      detect_intents("pull up the static accounts") == ["static_account"],
      repr(detect_intents("pull up the static accounts")))
# Token equality (never substring): 'count' must not match inside 'account'.
check("count not fuzzy-matched inside 'account number'",
      _phrase_similarity("get me the account number", ["count"])[0] < 0.75,
      str(_phrase_similarity("get me the account number", ["count"])))
# Typo-tolerant fuzzy tier (spaCy FUZZY / char-level best practice): a
# within-one-edit keyword ('sttic', 'emial', 'benficiary') still classifies,
# while word-form extensions ('county' vs count, 'estate' vs state) never do.
check("fuzzy: sttic account -> static_account",
      detect_intents("sttic account for 2103O338") == ["static_account"],
      repr(detect_intents("sttic account for 2103O338")))
check("fuzzy: emial -> email",
      detect_intents("get the emial for medplus") == ["email"],
      repr(detect_intents("get the emial for medplus")))
check("fuzzy: benficiary -> static_account",
      detect_intents("get the benficiary for MX141692")[0] == "static_account",
      repr(detect_intents("get the benficiary for MX141692")))
check("fuzzy: adress -> address",
      detect_intents("what is the adress of just chips") == ["address"],
      repr(detect_intents("what is the adress of just chips")))
# 'paybles' fuzzy-matches the 'payable' keyword of BOTH payable and
# static_account; static_account wins the tie and subsumes payable (its
# pipeline returns Payable Code) — so the request routes to static_account.
check("fuzzy: paybles -> static_account (payable subsumed)",
      detect_intents("get me the paybles for this merchant") == ["static_account"],
      repr(detect_intents("get me the paybles for this merchant")))
check("fuzzy: contcat -> contact",
      detect_intents("get me the contcat for 2ISW916B") == ["contact"],
      repr(detect_intents("get me the contcat for 2ISW916B")))
check("fuzzy: telphone -> phone (insertion)",
      detect_intents("telphone for MX141692") == ["phone"],
      repr(detect_intents("telphone for MX141692")))
check("fuzzy hit labelled ~fuzzy",
      any(m.startswith("~fuzzy")
          for m in _analyze("sttic account for 2103O338")["static_account"]["matched"]),
      repr(_analyze("sttic account for 2103O338")))
check("fuzzy: county never matches count",
      "count" not in detect_intents("get me the county of lagoons"),
      repr(detect_intents("get me the county of lagoons")))
check("fuzzy: estate never matches state",
      "state" not in detect_intents("what estate is lagoons in"),
      repr(detect_intents("what estate is lagoons in")))
check("fuzzy: double-s address stems correctly",
      _stem("address") == "address" and _stem("adress") == "adress",
      repr((_stem("address"), _stem("adress"))))
# The score cap (semantic max 4.0) guarantees a typo can NEVER outrank a
# real regex hit: 'email' (regex 5) beats 'sttic account' (fuzzy 4).
check("fuzzy never outranks a regex hit",
      detect_intents("get the email and sttic account for MX141692")
      == ["email", "static_account"],
      repr(detect_intents("get the email and sttic account for MX141692")))
check("account-number request routes to account_number",
      detect_intents("get me the account number for MX141692") == ["account_number"],
      repr(detect_intents("get me the account number for MX141692")))
# Bare identifiers are single tokens -> never boosted into an intent.
check("bare identifier never boosted",
      detect_intents("MX141692") == ["resolve"],
      repr(detect_intents("MX141692")))

# ── Per-intent fuzzy toggle (intents.json "fuzzy": false) ────────────────
# The semantic/typo tier is gated by INTENT_FUZZY (default True). Flipping
# one intent to False must restrict it to exact regex patterns only, while
# other intents keep typo tolerance.
from merchant_intelligence.tasks import vocab as _vocab_t
_fuzzy_backup = dict(_vocab_t.INTENT_FUZZY)
try:
    _vocab_t.INTENT_FUZZY["email"] = False
    check("fuzzy OFF: emial no longer classifies as email",
          "email" not in detect_intents("get the emial for medplus"),
          repr(detect_intents("get the emial for medplus")))
    check("fuzzy OFF: exact regex still classifies",
          "email" in detect_intents("get the email for medplus"),
          repr(detect_intents("get the email for medplus")))
    check("fuzzy OFF is per-intent (static_account unaffected)",
          "static_account" in detect_intents("sttic account for 2103O338"),
          repr(detect_intents("sttic account for 2103O338")))
finally:
    _vocab_t.INTENT_FUZZY.clear()
    _vocab_t.INTENT_FUZZY.update(_fuzzy_backup)
check("INTENT_FUZZY restored after test",
      _vocab_t.INTENT_FUZZY == _vocab_t._DEFAULT_INTENT_FUZZY,
      repr(_vocab_t.INTENT_FUZZY))
# The config file itself carries the toggle (defaults = all ON), so a
# non-developer can flip any intent via intents.json / the Rule Engine UI.
_cfg_f = _vocab_t.get_intent_config().get("intents") or {}
check("every config intent carries a bool fuzzy flag",
      all(isinstance((s or {}).get("fuzzy"), bool) for s in _cfg_f.values()),
      repr({k: (v or {}).get("fuzzy") for k, v in _cfg_f.items()}))
check("default specs include fuzzy",
      all(isinstance(s.get("fuzzy"), bool)
          for s in _vocab_t.default_intent_specs().values()))

# ── Request-slang expansion (intents.json "slang" map) ───────────────────
# Ops short-hands ('acct mgr', 'deets', 'stmnt', 'addr') normalise to their
# canonical form BEFORE regex + semantic tiers, so a slang request behaves
# exactly like the full word. Word-boundary + >= 3 char keys mean real
# names ('ADDIDE') and identifiers ('MX141692') can never be mangled.
from merchant_intelligence.tasks.intents import _normalize as _norm_slang
check("slang: deets -> details",
      _norm_slang("get me the deets for lagoons") == "get me the details for lagoons",
      repr(_norm_slang("get me the deets for lagoons")))
check("slang: acct mgr -> account manager",
      _norm_slang("acct mgr") == "account manager", repr(_norm_slang("acct mgr")))
check("slang: stmnt -> statement",
      _norm_slang("what stmnt is medplus on") == "what statement is medplus on",
      repr(_norm_slang("what stmnt is medplus on")))
check("slang never touches identifiers",
      _norm_slang("static acct for MX141692") == "static account for mx141692",
      repr(_norm_slang("static acct for MX141692")))
check("slang never touches real names",
      _norm_slang("ADDIDE APATA") == "addide apata",
      repr(_norm_slang("ADDIDE APATA")))
check("slang deets routes to profile",
      detect_intents("get me the deets for lagoons") == ["profile"],
      repr(detect_intents("get me the deets for lagoons")))
check("slang acct mgr routes to static_account",
      "static_account" in detect_intents("pull the acct mgr beneficiary for MX141692"),
      repr(detect_intents("pull the acct mgr beneficiary for MX141692")))
check("slang addr+mob routes to address",
      "address" in detect_intents("get the addr and mob for just chips"),
      repr(detect_intents("get the addr and mob for just chips")))
check("config carries the slang map",
      isinstance(_vocab_t.get_intent_config().get("slang"), dict)
      and len(_vocab_t.get_intent_config().get("slang") or {}) >= 5,
      repr(list(_vocab_t.get_intent_config().get("slang") or {}))[:80])
check("live slang matches defaults",
      _vocab_t.INTENT_SLANG == _vocab_t._DEFAULT_SLANG,
      repr(_vocab_t.INTENT_SLANG))
check("slang in segment path routes collection",
      (detect_task("get me all the addr of all nnpc stations") or {}).get("intent")
      == "segment",
      repr((detect_task("get me all the addr of all nnpc stations") or {}).get("intent")))
check("slang stmnt routes to statement/account",
      bool(detect_intents("what stmnt is medplus on")),
      repr(detect_intents("what stmnt is medplus on")))

# analyze() exposes excluded + workflow for the UI/debug panel.
_an = _analyze_pub("get account details for 2103O338 but not the change history")
check("analyze exposes excluded",
      _an.get("excluded") == ["change_details"], repr(_an.get("excluded")))
check("analyze exposes workflow",
      isinstance(_an.get("workflow"), dict) and _an.get("workflow"),
      repr(_an.get("workflow")))

# analyze() exposes the clarification field (the Rule Engine test panel
# renders it): ambiguous requests carry question + options, decisive ones
# carry None, and a remembered choice surfaces as auto_pick.
_an_clar = _analyze_pub("get account details for medplus")
check("analyze exposes clarification for ambiguous requests",
      isinstance(_an_clar.get("clarification"), dict)
      and bool(_an_clar["clarification"].get("question"))
      and len(_an_clar["clarification"].get("options", [])) >= 2,
      repr(_an_clar.get("clarification")))
check("clarification options carry intent + label",
      all(o.get("intent") and o.get("label")
          for o in (_an_clar.get("clarification") or {}).get("options", [])),
      repr(_an_clar.get("clarification")))
check("analyze clarification is None for decisive requests",
      _analyze_pub("get the static account for MX183544").get("clarification") is None)
check("analyze clarification is None for plain names",
      _analyze_pub("LAGOON WATERS").get("clarification") is None)

# ── Clarification engine (ambiguous requests ask which interpretation) ────
print("\n[4k] clarification engine (ambiguous -> ask, decisive -> run)")
from merchant_intelligence.tasks import suggest_clarification as _suggest_clarify

# "account details" races change_details (4.0) vs profile (3.0) — the app
# must ask rather than guess.
_clar = _suggest_clarify("get account details for medplus")
check("ambiguous account-details asks for clarification",
      _clar is not None and _clar.get("question") and _clar.get("options"),
      repr(_clar))
if _clar:
    _clar_ints = {o["intent"] for o in _clar["options"]}
    check("clarification offers change_details + profile",
          {"change_details", "profile"} <= _clar_ints, repr(_clar_ints))
    check("clarification options have labels + descriptions",
          all(o.get("label") and o.get("description") for o in _clar["options"]))

# Lone vague intent ("bank details" -> profile via generic 'details') also asks.
_clar2 = _suggest_clarify("get the bank details of lagoons")
check("vague lone intent asks for clarification",
      _clar2 is not None and len(_clar2["options"]) >= 2, repr(_clar2))

# Decisive requests never clarify.
check("decisive static-account request never clarifies",
      _suggest_clarify("get the static account for MX183544") is None)
check("decisive profile request never clarifies",
      _suggest_clarify("get me all the information on medplus") is None)
check("decisive change-request never clarifies",
      _suggest_clarify("give me the change of account details of just chips") is None)
check("decisive phone request never clarifies",
      _suggest_clarify("get the phone number of MX141692") is None)
check("plain name never clarifies",
      _suggest_clarify("LAGOON WATERS") is None)
check("segment request never clarifies",
      _suggest_clarify("get me all the addresses of all nnpc stations") is None)
# The risky false-positive path: a COMPOUND request where two strong intents
# race (gap <= 4) — the user asked for BOTH, so confidence >= 60 must keep
# it decisive (no clarification).
check("compound two-intent request never clarifies",
      _suggest_clarify("get the static account and the change details of MX183639") is None)
check("compound email+phone request never clarifies",
      _suggest_clarify("get the emails and phones for MX141692") is None)

# intent_override forces exactly the chosen interpretation.
_ov = detect_task("get account details for medplus", intent_override="static_account")
check("override forces static_account",
      _ov is not None and _ov["intent"] == "static_account"
      and _ov["intents"] == ["static_account"], repr(_ov))
check("override never forces an excluded intent",
      detect_task("get account details for medplus but not the change history",
                  intent_override="change_details")["intent"] != "change_details")
check("override unknown intent is ignored",
      detect_task("get account details for medplus",
                  intent_override="not_an_intent")["intent"] == "change_details")
check("override keeps identifiers",
      (detect_task("get account details for MX141692",
                   intent_override="static_account") or {}).get("identifiers", {})
      .get("mxcode") == ["MX141692"])

# ── Confidence calibration (fit ask thresholds from real usage) ──────────
print("\n[4l] calibration (decision log + threshold fitter)")
import tempfile as _tempfile
from merchant_intelligence import calibration as _cal

_tmp_cal = _Path(_tempfile.gettempdir()) / "_probe_calibration.jsonl"
_tmp_cal.unlink(missing_ok=True)
_old_cal_env = _os.environ.get("MERCHANT_CALIBRATION_FILE")
_os.environ["MERCHANT_CALIBRATION_FILE"] = str(_tmp_cal)

# Empty log -> inactive, defaults used.
check("calibration inactive with no data",
      _cal.params()["active"] is False and _cal.params()["ask_threshold"] == 60)

# Record decisions: auto-runs (accepted) and a clarification pick (corrected).
_cal.record("get the static account for MX1", "static_account", 96,
            "static_account", source="auto")
_cal.record("get the email for MX2", "email", 60, "email", source="auto")
_cal.record("get account details for X", "change_details", 48,
            "static_account", source="override")  # user corrected a low-conf pick
_s = _cal.stats()
check("calibration logs decisions", _s["decisions"] == 3, repr(_s))
check("calibration counts accepted", _s["accepted"] == 2, repr(_s))
check("calibration splits sources (auto vs override)",
      _s["sources"]["auto"] == 2 and _s["sources"]["override"] == 1
      and _s["sources"]["accept"] == 0, repr(_s["sources"]))

# Not enough samples -> still inactive (threshold stays default).
check("calibration inactive under min samples",
      _cal.params()["active"] is False and _cal.params()["ask_threshold"] == 60)

# Enough samples -> fitted. High-confidence auto-accepted requests alone are
# NOT enough to move the threshold (no evidence at lower bands).
for i in range(22):
    _cal.record(f"auto-request-{i}", "static_account", 90 + (i % 10),
                "static_account", source="auto")
_p = _cal.params()
check("calibration activates past min samples", _p["active"] is True, repr(_p))
check("solid top band alone keeps the default threshold",
      _p["ask_threshold"] == 60, repr(_p))

# Users CONFIRMING the engine's pick at low confidence is evidence the
# engine can be trusted there -> threshold drops below default.
for i in range(15):
    _cal.record(f"confirmed-low-{i}", "profile", 25 + (i % 14),
                "profile", source="accept")  # user agreed with the pick
_p2 = _cal.params()
check("confirmed low-confidence picks lower the ask threshold",
      _p2["ask_threshold"] < 60, repr(_p2))

# WIRING CHECK (at the lowered threshold): "get the bank details of
# lagoons" is profile conf 36. With the ask threshold at 20 the engine
# must RUN IT DIRECTLY instead of asking — proving suggest_clarification
# consults the live calibration, not the hardcoded default.
check("low fitted threshold lets a conf-36 request run directly",
      _suggest_clarify("get the bank details of lagoons") is None,
      repr(_cal.params()))

# A correction cluster in the mid band must RAISE it again (requests around
# that confidence get flagged before running).
for i in range(12):
    _cal.record(f"shaky-request-{i}", "profile", 42 + (i % 8),
                "change_details", source="override")  # user corrected
_p3 = _cal.params()
check("corrections raise the ask threshold",
      _p3["ask_threshold"] > _p2["ask_threshold"], repr(_p3))

# WIRING CHECK (after the raise): the same conf-36 request must ASK again.
check("raised threshold flags the same request again",
      _suggest_clarify("get the bank details of lagoons") is not None,
      repr(_cal.params()))

# fit() exposes bands + per-intent acceptance for the UI.
_f = _cal.fit()
check("fit exposes confidence bands",
      isinstance(_f.get("ask_threshold"), int) and _f["samples"] >= 20)
check("stats expose per-intent acceptance",
      any(b["intent"] == "static_account" and b["samples"] > 0
          for b in _cal.stats()["per_intent"]))

# reset() clears the log back to defaults.
_n = _cal.reset()
check("calibration reset removes entries", _n > 0 and _cal.params()["active"] is False,
      f"removed={_n}")

# Accept vs override tagging (on the clean log, so the band-scan fit above
# is unaffected): a pick that MATCHES the prediction is an accept (user
# confirmed), one that differs is an override (user corrected).
_cal.record("get the email for MX2b", "email", 55, "email", source="accept")
_cal.record("get account details for Y", "change_details", 48,
            "static_account", source="override")
_s_tag = _cal.stats()
check("accept source counted separately",
      _s_tag["sources"]["accept"] == 1 and _s_tag["sources"]["override"] == 1
      and _s_tag["sources"]["auto"] == 0, repr(_s_tag["sources"]))
check("accept-pick counts as accepted, override as corrected",
      _s_tag["accepted"] == 1 and _s_tag["acceptance"] == 0.5,
      repr(_s_tag))
# Legacy "clarify" entries (recorded before the accept/override split) fold
# into the right bucket via their accepted flag — nothing is lost on upgrade.
_cal.record("legacy confirmed", "profile", 40, "profile", source="clarify")
_cal.record("legacy corrected", "profile", 40, "change_details", source="clarify")
_s_leg = _cal.stats()
check("legacy clarify folds into accept/override buckets",
      _s_leg["sources"]["accept"] == 2 and _s_leg["sources"]["override"] == 2,
      repr(_s_leg["sources"]))
# The source buckets always reconcile to the decision count (an "other"
# bucket absorbs unknown sources so the UI totals never silently disagree).
check("source buckets sum to decisions",
      sum(_s_leg["sources"].values()) == _s_leg["decisions"],
      repr((_s_leg["sources"], _s_leg["decisions"])))

# ── Race-window fit (gap_threshold): logged race outcomes tune how close
# the top two intents must be for the engine to ask. A race decision carries
# the top-2 score gap it was asked at — accept = the ask was unnecessary
# (tighten the window), override = it was justified (keep it that wide).
_cal.record("race-log-1", "profile", 50, "profile", source="accept", gap=3.5)
_g_last = _cal.load()[-1]
check("race outcome logs the top-2 score gap",
      _g_last.get("gap") == 3.5, repr(_g_last))
# 22 ACCEPTED races in the 2-3 gap band: solid evidence the engine can
# auto-run races there -> the window tightens from the 4.0 default to 2.0.
for i in range(22):
    _cal.record(f"race-tight-{i}", "profile", 62 + (i % 5),
                "profile", source="accept", gap=2.5)
_p_gap = _cal.params()
check("race outcomes activate the gap fit",
      _p_gap.get("gap_active") is True, repr(_p_gap))
check("accepted races tighten the race window",
      _p_gap["gap_threshold"] < 4.0, repr(_p_gap))
_s_gap = _cal.stats()
check("stats expose race decisions + gap bands",
      _s_gap["race_decisions"] >= 23
      and isinstance(_s_gap.get("gap_bands"), list)
      and any(b["band"] == "2-3" and b["samples"] == 22
              and b["acceptance"] == 1.0 for b in _s_gap["gap_bands"]),
      repr(_s_gap.get("gap_bands")))
_f_gap = _cal.fit()
check("fit exposes fitted gap threshold + race samples",
      _f_gap.get("gap_active") is True
      and _f_gap.get("race_samples") == _s_gap["race_decisions"]
      and _f_gap["gap_threshold"] == 2.0, repr(_f_gap))

# WIRING CHECK (at the tightened window): a race whose top-2 gap is INSIDE
# the fitted 2.0 window must still ask, while a gap-3.0 race (outside it,
# but inside the 4.0 default) must run directly — proving
# suggest_clarification consults the fitted gap, not the hardcoded default.
# Preferences point at a temp store so a real saved interpretation can never
# flip these checks.
_tmp_pref_gap = _Path(_tempfile.gettempdir()) / "_probe_prefs_gap.json"
_tmp_pref_gap.unlink(missing_ok=True)
_old_pref_gap_env = _os.environ.get("MERCHANT_PREFERENCES_FILE")
_os.environ["MERCHANT_PREFERENCES_FILE"] = str(_tmp_pref_gap)
try:
    _craft1 = detect_task("get the emails and phones for MX141692", use_llm=False)
    _craft1["analysis"] = {"email": {"score": 4.0}, "phone": {"score": 3.0}}
    _cl_gap = _suggest_clarify("get the emails and phones for MX141692", _craft1)
    check("race within fitted window still asks",
          _cl_gap is not None and _cl_gap.get("question"), repr(_cl_gap))
    _craft2 = detect_task("get the emails and phones for MX141692", use_llm=False)
    _craft2["analysis"] = {"email": {"score": 4.0}, "phone": {"score": 1.0}}
    check("race beyond fitted window runs directly",
          _suggest_clarify("get the emails and phones for MX141692", _craft2) is None,
          repr(_cal.params()))
finally:
    if _old_pref_gap_env is None:
        _os.environ.pop("MERCHANT_PREFERENCES_FILE", None)
    else:
        _os.environ["MERCHANT_PREFERENCES_FILE"] = _old_pref_gap_env
    _tmp_pref_gap.unlink(missing_ok=True)

# Cleanup: restore env + temp file.
if _old_cal_env is None:
    _os.environ.pop("MERCHANT_CALIBRATION_FILE", None)
else:
    _os.environ["MERCHANT_CALIBRATION_FILE"] = _old_cal_env
_tmp_cal.unlink(missing_ok=True)
check("calibration env restored",
      str(_cal._log_path()) != str(_tmp_cal))

# ── Saved interpretations ("remember my choice") ────────────────────────
print("\n[4m] preferences (phrase-key normalization + remember/forget)")
from merchant_intelligence import preferences as _prefs

_tmp_pref = _Path(_tempfile.gettempdir()) / "_probe_preferences.json"
_tmp_pref.unlink(missing_ok=True)
_old_pref_env = _os.environ.get("MERCHANT_PREFERENCES_FILE")
_os.environ["MERCHANT_PREFERENCES_FILE"] = str(_tmp_pref)

# Phrase keys: merchant names / identifiers / filler words are stripped so
# the SAME ambiguous phrase across different merchants shares one key.
_tp1 = detect_task("get account details for medplus")
_tp2 = detect_task("get the account details of lagoons")
_k1 = _prefs.phrase_key("get account details for medplus", _tp1)
_k2 = _prefs.phrase_key("get the account details of lagoons", _tp2)
check("phrase keys match across merchants", _k1 == _k2 == "account details",
      f"{_k1!r} vs {_k2!r}")
_k3 = _prefs.phrase_key("MX141692 get the account details",
                        detect_task("MX141692 get the account details"))
check("identifier-only phrase keeps the intent words",
      _k3 == "account details", repr(_k3))
check("empty store returns nothing",
      _prefs.lookup("get account details for medplus", _tp1) is None)
check("learn stores phrase -> intent",
      _prefs.learn("get account details for medplus", "static_account", _tp1)
      == "account details")
check("same phrase on another merchant auto-picks",
      _prefs.lookup("get the account details of lagoons", _tp2)
      == "static_account")
check("different phrase does not match",
      _prefs.lookup("get the bank details of lagoons",
                    detect_task("get the bank details of lagoons")) is None)
check("forget removes the saved key",
      _prefs.forget("account details") is True
      and _prefs.lookup("get account details for medplus", _tp1) is None)
check("forget missing key returns False",
      _prefs.forget("account details") is False)

# Wiring: a learned phrase makes suggest_clarification auto-pick (no card).
_prefs.learn("get account details for medplus", "static_account", _tp1)
_clar_auto = _suggest_clarify("get the account details of lagoons")
check("remembered phrase auto-picks (no card)",
      _clar_auto is not None and _clar_auto.get("auto_pick") == "static_account",
      repr(_clar_auto))
check("auto-pick still carries question + options",
      bool(_clar_auto and _clar_auto.get("question"))
      and bool(_clar_auto and _clar_auto.get("options")))
# A DECISIVE request never auto-picks — remember only shortcuts ambiguity.
check("decisive request never auto-picks",
      _suggest_clarify("get the static account for MX183544") is None)
# A negated variant of the phrase has a different key ("... not the change
# history") and must NOT blindly reuse the remembered choice.
_clar_neg = _suggest_clarify(
    "get the account details of lagoons but not the change history")
check("negated variant does not reuse the remembered choice",
      _clar_neg is None or _clar_neg.get("auto_pick") is None,
      repr(_clar_neg))

# reset clears everything
_n_pref = _prefs.reset()
check("preferences reset removes entries",
      _n_pref >= 1 and _prefs.all_prefs() == {}, f"removed={_n_pref}")

# Cleanup: restore env + temp file.
if _old_pref_env is None:
    _os.environ.pop("MERCHANT_PREFERENCES_FILE", None)
else:
    _os.environ["MERCHANT_PREFERENCES_FILE"] = _old_pref_env
_tmp_pref.unlink(missing_ok=True)
check("preferences env restored",
      str(_prefs._path()) != str(_tmp_pref))

# ── Pipeline execution against the live registry ──────────────────────────
print("\n[4] execute_task (live intelligence.db)")
# The user's exact scenario — TIDs that exist in the static-acct terminal data.
example = ("2103O338\tFELIX OKONMAH\n2103O340\tADEBOWALE FESOMADE\n"
           "2103O341\tADEBOWALE FESOMADE\n2103O342\tGEORGE ONORIODE\n"
           "2103O343\tGEORGE ONORIODE\n"
           "Pls get this merchant MXCODE, then use the mxcode to get the "
           "above merchant static account and the beneficiary name from "
           "static acct manager")
t = detect_task(example)
check("detected as task", t is not None)
res = execute_task(t)
check("static_account pipeline ran", res["intent"] == "static_account", res["intent"])
check("pipeline steps listed", "static_account" in res.get("pipeline", []),
      repr(res.get("pipeline")))
check("produced rows", len(res["rows"]) >= 4, f"{len(res['rows'])} rows")
if res["rows"]:
    r0 = res["rows"][0]
    check("row has TID", r0.get("tid"), repr(r0.get("tid")))
    check("row has MX code", str(r0.get("mxcode", "")).startswith("MX"),
          repr(r0.get("mxcode")))
    check("row has static account number", bool(r0.get("static_acc_no")),
          repr(r0.get("static_acc_no")))
    check("row has beneficiary", bool(r0.get("beneficiary")),
          repr(r0.get("beneficiary")))
    check("row has bank", bool(r0.get("bank")), repr(r0.get("bank")))

# MX codes from the earlier live lookup should resolve to static accounts
t2 = detect_task("MX184382\nMX184383\nstatic account and beneficiary")
res2 = execute_task(t2)
check("MX-direct task detected", t2 is not None)
check("MX-direct produced rows", len(res2["rows"]) >= 1, f"{len(res2['rows'])}")

# Generic resolve pipeline
t3 = detect_task("2103O338\nMX184380")
res3 = execute_task(t3)
check("resolve pipeline intent", res3.get("intent") == "resolve", res3.get("intent"))
check("resolve produced rows", len(res3["rows"]) >= 1, f"{len(res3['rows'])}")

# A pasted STATIC ACCOUNT NUMBER must resolve (DB probe showed static_acc_no
# and account_number have ZERO overlap, so both columns are searched).
t4 = detect_task("5180857349\nshow me the merchant for this static account")
res4 = execute_task(t4)
check("static-acc-number task detected", t4 is not None)
check("static acc no resolves to a row", len(res4["rows"]) >= 1, f"{len(res4['rows'])}")
if res4["rows"]:
    check("static acc row has merchant", bool(res4["rows"][0].get("merchant")))

# ── Workflow execution: the dependency-aware plan actually runs ──────────
print("\n[4a] execute_task runs the dependency-aware workflow")
# Name-only static account: the plan declares resolve_mxcode ->
# fetch_static_account; the executor synthesizes the resolve step and feeds
# the produced MX codes into the static-account step.
w1 = detect_task("get the static account for LAGOON WATERS")
check("name-only static account detected", w1 is not None
      and w1.get("intent") == "static_account",
      repr(w1 and w1.get("intent")))
rw1 = execute_task(w1)
check("workflow ran with synthesized resolve step",
      rw1.get("workflow_executed") == ["resolve_mxcode", "fetch_static_account"],
      repr(rw1.get("workflow_executed")))
check("produced mxcodes threaded into the dependent step",
      bool(rw1.get("workflow_chain", {}).get("fetch_static_account")),
      repr(rw1.get("workflow_chain")))
check("chained rows carry MX codes", bool(rw1["rows"])
      and all((r.get("mxcode") or "").startswith("MX")
              for r in rw1["rows"] if r.get("mxcode")),
      f"{len(rw1['rows'])} rows")
# Identifier-only static account: the pipeline resolves the MX requirement
# internally, so no resolve step is synthesized and the run is unchanged.
rw2 = execute_task(detect_task("get the static account for MX183544"))
check("identifier-only plan runs its one step",
      rw2.get("workflow_executed") == ["fetch_static_account"],
      repr(rw2.get("workflow_executed")))
check("identifier-only plan threads nothing",
      rw2.get("workflow_chain") == {}, repr(rw2.get("workflow_chain")))
# No-edge compound (mxcode + email): both steps run, nothing is threaded.
w3 = detect_task("2103O338\nMX184380\nget the mx codes and emails for these")
rw3 = execute_task(w3)
check("no-edge compound runs both plan steps",
      set(rw3.get("workflow_executed")) == {"resolve_mxcode", "fetch_email"},
      repr(rw3.get("workflow_executed")))
check("no-edge compound threads nothing",
      rw3.get("workflow_chain") == {}, repr(rw3.get("workflow_chain")))
check("no-edge compound merged rows", len(rw3["rows"]) >= 1,
      f"{len(rw3['rows'])} rows")

# ── Segment / collection intents (feature: "all the addresses of all nnpc")
print("\n[4b] segment intent (collection requests)")
seg = detect_task("get me all the addresses of all nnpc stations")
check("segment request IS a task", seg is not None)
check("segment intent detected", (seg or {}).get("intent") == "segment",
      repr((seg or {}).get("intent")))
if seg:
    check("segment fragment extracted", seg.get("segment") == "NNPC",
          repr(seg.get("segment")))
    check("segment field extracted", "address" in (seg.get("segment_fields") or []),
          repr(seg.get("segment_fields")))
    check("segment never leaks names", seg.get("names") == [],
          repr(seg.get("names")))
    rseg = execute_task(seg)
    check("segment pipeline ran", rseg["intent"] == "segment")
    check("segment returns many rows", len(rseg["rows"]) >= 600,
          f"{len(rseg['rows'])} rows")
    check("segment columns include Address + Merchant",
          "Address" in rseg["columns"] and "Merchant" in rseg["columns"],
          repr(rseg["columns"]))
    if rseg["rows"]:
        check("segment row has merchant", bool(rseg["rows"][0].get("merchant")))
    check("segment summary has count", str(len(rseg["rows"])) in rseg["summary"],
          rseg["summary"])

seg2 = detect_task("get me all the emails of all mrsp merchants")
check("emails-segment detected", (seg2 or {}).get("intent") == "segment")
if seg2:
    check("emails-segment fragment MRSP", seg2.get("segment") == "MRSP",
          repr(seg2.get("segment")))
    check("emails-segment field email", "email" in (seg2.get("segment_fields") or []))

seg3 = detect_task("get me all the phones in the NNPC PARAMETER FILE BATCH sheet")
check("sheet-segment detected", (seg3 or {}).get("intent") == "segment")
if seg3:
    r3 = execute_task(seg3)
    check("sheet-segment matches NNPC batch rows", len(r3["rows"]) >= 200,
          f"{len(r3['rows'])} rows")

# Segmentation must NOT hijack per-merchant profile requests or plain names
seg4 = detect_task("get me all the information on LAGOON WATERS")
check("per-merchant profile NOT segment",
      seg4 is not None and seg4.get("intent") == "profile",
      repr((seg4 or {}).get("intent")))
# Profile request whose merchant NAME contains a field word must stay a
# profile ("bank"/"stores"/"station" are field words, but here they are
# part of the merchant name, not the requested field).
seg5 = detect_task("get me all the information on IBADAN STORE")
check("profile w/ field-word merchant name stays profile",
      seg5 is not None and seg5.get("intent") == "profile",
      repr((seg5 or {}).get("intent")))
seg6 = detect_task("show me everything about LAGOON WATER STORES")
check("profile w/ STORES merchant stays profile",
      seg6 is not None and seg6.get("intent") == "profile",
      repr((seg6 or {}).get("intent")))
check("plain name ALL STAR STORES not a task",
      detect_task("ALL STAR STORES") is None)
check("extract_segment strips field words",
      extract_segment("get me all the addresses of all nnpc stations")
      == ("NNPC", ["address"]),
      repr(extract_segment("get me all the addresses of all nnpc stations")))

# 'all the <field> for <merchant>' is a FIELD request for one merchant, not
# a 1000-row segment dump: the segment gate needs a collective noun
# (stations/stores/merchants/…) or the 'of all' pattern to fire.
segA = detect_task("get me all the tid for medplus")
check("all-tid-for-medplus -> tid NOT segment",
      segA is not None and segA.get("intent") == "tid"
      and segA.get("names") == ["MEDPLUS"],
      repr((segA or {}).get("intent")))
segB = detect_task("give me all the tid for addide")
check("all-tid-for-addide -> tid NOT segment",
      segB is not None and segB.get("intent") == "tid",
      repr((segB or {}).get("intent")))
# Genuine collections still route to segment (collective noun present).
check("all-stores-for-medplus still segment",
      (detect_task("get me all the stores for medplus") or {}).get("intent")
      == "segment")
check("all-tids-of-nnpc-merchants still segment",
      (detect_task("all the tids of the nnpc merchants") or {}).get("intent")
      == "segment")
check("all-addresses-of-all-nnpc-stations still segment",
      (detect_task("get all the addresses of all nnpc stations") or {}).get("intent")
      == "segment")
# Live: the tid pipeline returns the merchant's FULL distinct terminal list.
if segA:
    rA = execute_task(segA)
    tids = [r.get("tid") or r.get("TID") or "" for r in rA["rows"]]
    tids = [t.strip() for t in tids if t.strip()]
    check("all-tid-for-medplus full list (>= 100 rows)",
          len(rA["rows"]) >= 100, f"{len(rA['rows'])} rows")
    check("all-tid-for-medplus no duplicate TIDs",
          len(tids) == len(set(tids)),
          f"{len(tids)} rows vs {len(set(tids))} distinct")

# The same full-list-per-terminal rule applies to the address intent:
# 'get me all the addresses for medplus' returns one row per terminal with
# its own address, not the top-8 search rows.
segC = detect_task("get me all the addresses for medplus")
check("all-addresses-for-medplus -> address intent",
      segC is not None and segC.get("intent") == "address",
      repr((segC or {}).get("intent")))
if segC:
    rC = execute_task(segC)
    addrs = [r.get("address") or r.get("Address") or "" for r in rC["rows"]]
    tidsC = [str(r.get("tid") or r.get("TID") or "").strip()
             for r in rC["rows"] if r.get("tid") or r.get("TID")]
    check("all-addresses-for-medplus full terminal list (>= 100 rows)",
          len(rC["rows"]) >= 100, f"{len(rC['rows'])} rows")
    check("all-addresses-for-medplus no duplicate TIDs",
          len(tidsC) == len(set(tidsC)),
          f"{len(tidsC)} rows vs {len(set(tidsC))} distinct")
    check("all-addresses-for-medplus every row has an address",
          all(a.strip() for a in addrs),
          f"{sum(1 for a in addrs if a.strip())}/{len(addrs)} rows")
# Two terminals sharing ONE address must BOTH appear (dedupe keys on the
# TID, not the address value).
segD = detect_task("get the address for addide abaranje")
if segD:
    rD = execute_task(segD)
    d_tids = {str(r.get("tid") or r.get("TID") or "").strip()
              for r in rD["rows"] if r.get("tid") or r.get("TID")}
    check("shared-address terminals both shown (2ISW971B + 2ISW417C)",
          {"2ISW971B", "2ISW417C"} <= d_tids, repr(sorted(d_tids)))
# The address-PASTE path is untouched: pasted address strings still match
# the address column with address_match status, never the full-list branch.
segE = detect_task("get me the tids for BRITISH INTERNATIONAL SCHOOL ROAD, LEKKI, LAGOS")
check("address paste still names_are_addresses",
      segE is not None and segE.get("names_are_addresses") is True,
      repr(segE and segE.get("names_are_addresses")))
if segE:
    rE = execute_task(segE)
    check("address paste resolves by address column",
          bool(rE["rows"]) and all(r.get("status") == "address_match"
                                   for r in rE["rows"]),
          repr([r.get("status") for r in (rE.get("rows") or [])[:3]]))

# ── Field-request noise stripping (regression: "get me the TID for X")
print("\n[4c2] field-request noise stripping")
from merchant_intelligence.matcher import strip_query_noise
from merchant_intelligence.search import MerchantSearch as _MSearch

# The user's exact regression: "get me the TID for nnpc apata" must search
# for the MERCHANT (nnpc apata), never match the TID token against stored
# TID values (which lifted ADDIDE APATA above the real APATA SS - NNPC).
check("field request strips TID (get me the TID for X)",
      strip_query_noise("get me the TID for nnpc apata") == "nnpc apata",
      repr(strip_query_noise("get me the TID for nnpc apata")))
check("field request strips email (show me the email of X)",
      strip_query_noise("show me the email of medplus") == "medplus",
      repr(strip_query_noise("show me the email of medplus")))
check("field request strips bank (get the bank of X)",
      strip_query_noise("get the bank of access bank") == "access bank",
      repr(strip_query_noise("get the bank of access bank")))
check("field request strips mxcode (what is the mxcode for X)",
      "lagoon waters" in strip_query_noise("what is the mxcode for lagoon waters").lower(),
      repr(strip_query_noise("what is the mxcode for lagoon waters")))
check("field request strips tid with 'from' preposition",
      strip_query_noise("get the tid from nnpc apata") == "nnpc apata",
      repr(strip_query_noise("get the tid from nnpc apata")))
# Merchant names that CONTAIN field words must survive intact — the
# positional pattern never touches them (the old global-word-list approach
# broke "FIRST BANK" by stripping BANK globally).
check("merchant name FIRST BANK keeps BANK",
      strip_query_noise("get me the profile of FIRST BANK") == "FIRST BANK",
      repr(strip_query_noise("get me the profile of FIRST BANK")))
check("field word inside name survives (on access bank)",
      strip_query_noise("get me all the information on access bank") == "access bank",
      repr(strip_query_noise("get me all the information on access bank")))
check("plain name ACCESS BANK untouched (no NL trigger)",
      strip_query_noise("ACCESS BANK") == "ACCESS BANK",
      repr(strip_query_noise("ACCESS BANK")))
check("no NL trigger -> untouched (the bank of america)",
      strip_query_noise("the bank of america") == "the bank of america",
      repr(strip_query_noise("the bank of america")))
check("no NL trigger -> untouched (NNPC APATA)",
      strip_query_noise("NNPC APATA") == "NNPC APATA",
      repr(strip_query_noise("NNPC APATA")))
# Live end-to-end: the NNPC station must win over the ADDIDE lookalike.
_msearch = _MSearch()
_nnpc_results = _msearch.search("get me the TID for nnpc apata", limit=5)
check("TID-request search surfaces APATA SS - NNPC first",
      bool(_nnpc_results) and _nnpc_results[0].record.get("merchant_name")
      == "APATA SS - NNPC",
      repr([r.record.get("merchant_name") for r in _nnpc_results[:3]]))
check("TID-request top score is decisive",
      bool(_nnpc_results) and _nnpc_results[0].overall_score >= 80,
      repr(_nnpc_results[0].overall_score if _nnpc_results else None))
_mx_results = _msearch.search("what is the mxcode for lagoon waters", limit=3)
check("mxcode-request surfaces LAGOON WATERS first",
      bool(_mx_results) and "LAGOON WATERS" in str(_mx_results[0].record.get("merchant_name")),
      repr([r.record.get("merchant_name") for r in _mx_results[:2]]))

# ── Key-merchant intent emphasis (MEDPLUS / ADDIDE / SPAR / ARTEE / …)
print("\n[4c3] key-merchant intents (MEDPLUS, ADDIDE, SPAR/ARTEE)")
from merchant_intelligence.tasks.parser import _match_key_merchant
from merchant_intelligence.tasks.vocab import KEY_MERCHANT_ROOTS as _KM_ROOTS

check("key-merchant roots defined", len(_KM_ROOTS) >= 8, repr(_KM_ROOTS))
check("root match: exact name", _match_key_merchant("MEDPLUS"))
check("root match: chain branch prefix", _match_key_merchant("ADDIDE APATA"))
check("root match: SPAR branch", _match_key_merchant("SPAR LEKKI"))
check("root match: ARTEE family", _match_key_merchant("ARTEE INDUSTRIES LIMITED"))
check("root match: non-key name rejected", not _match_key_merchant("PALM GROVE"))

# everything-on/for/regarding phrasing now routes to profile
_kp1 = detect_task("get me everything on spar")
check("everything-on spar -> profile",
      _kp1 is not None and _kp1.get("intent") == "profile"
      and _kp1.get("names") == ["SPAR"],
      repr((_kp1 or {}).get("names")))
check("everything-on artee -> profile",
      (detect_task("get me everything on artee industries limited") or {})
      .get("intent") == "profile")
check("give-everything-on cascades -> profile",
      (detect_task("give me everything on cascades luxury") or {}).get("intent")
      == "profile")
check("everything-about medplus still profile",
      (detect_task("get me everything about medplus") or {}).get("intent")
      == "profile")

# Question-form bank requests route to the bank pipeline
_kb1 = detect_task("what is the bank for just chips")
check("what-is-the-bank-for -> bank",
      _kb1 is not None and _kb1.get("intent") == "bank"
      and _kb1.get("names") == ["JUST CHIPS"],
      repr((_kb1 or {}).get("names")))
_kb2 = detect_task("which bank does addide use")
check("which-bank-does-X-use -> bank",
      _kb2 is not None and _kb2.get("intent") == "bank"
      and _kb2.get("names") == ["ADDIDE"],
      repr((_kb2 or {}).get("names")))
check("which-bank-is-medplus-with -> bank",
      (detect_task("which bank is medplus with") or {}).get("intent") == "bank")

# Bare key-merchant shorthand (no instruction verb, no question word)
_kb3 = detect_task("medplus emails")
check("medplus emails -> email task",
      _kb3 is not None and _kb3.get("intent") == "email"
      and _kb3.get("names") == ["MEDPLUS"],
      repr((_kb3 or {}).get("names")))
check("addide addresses -> address task",
      (detect_task("addide addresses") or {}).get("intent") == "address")
check("spar phone number -> phone task",
      (detect_task("spar phone number") or {}).get("intent") == "phone")

# Field-word name cleanup: "get medplus phone and email" must search MEDPLUS,
# not "MEDPLUS PHONE" — while a real merchant containing a field word
# (ACCESS BANK) keeps its full name.
_kb4 = detect_task("get medplus phone and email")
check("medplus phone+email compound keeps clean name",
      _kb4 is not None and _kb4.get("names") == ["MEDPLUS"]
      and set(_kb4.get("intents", [])) >= {"email", "phone"},
      repr((_kb4 or {}).get("names")))
check("ACCESS BANK keeps full name (bank not in FIELD_NAME_STOPS)",
      (detect_task("get me the bank for access bank") or {}).get("names")
      == ["ACCESS BANK"])
# Field-word cleanup strips only TRAILING field words — a merchant whose
# name CONTAINS the field word in the middle keeps its full name.
check("SUN PHONE STORE keeps mid-word PHONE (trailing-only strip)",
      (detect_task("get me the phone for SUN PHONE STORE") or {}).get("names")
      == ["SUN PHONE STORE"])
check("AUTO MAIL keeps mid-word MAIL (trailing-only strip)",
      (detect_task("get me the email for AUTO MAIL") or {}).get("names")
      == ["AUTO MAIL"])
check("NEW STATE HOTELS keeps mid-word STATE",
      (detect_task("get the state of NEW STATE HOTELS") or {}).get("names")
      == ["NEW STATE HOTELS"])

# Key-merchant weak-all segments: "all addide stores in lagos"
_kb5 = detect_task("all addide stores in lagos")
check("all-addide-stores-in-lagos -> segment ADDIDE",
      _kb5 is not None and _kb5.get("intent") == "segment"
      and _kb5.get("segment") == "ADDIDE"
      and (_kb5.get("params") or {}).get("state") == "LAGOS",
      repr((_kb5 or {}).get("segment")))

# Guards: generic names with field words must STAY normal searches
check("non-key 'medplus bank' stays a search", detect_task("medplus bank") is None)
check("PALM GROVE ADDRESS stays a search",
      detect_task("palm grove address") is None)
check("BANK OF INDUSTRY stays a search", detect_task("BANK OF INDUSTRY") is None)
check("ALL STAR STORES stays a search", detect_task("ALL STAR STORES") is None)
check("profile of FIRST BANK keeps full name",
      (detect_task("get me the profile of first bank") or {}).get("names")
      == ["FIRST BANK"])

# ── Key-merchant enhancement pass: profile shorthand, typo tolerance ────
from merchant_intelligence.tasks.parser import key_merchant_matches as _km_matches

# Profile shorthand: "medplus profile" / "spar full profile" / reverse order
_kp2 = detect_task("medplus profile")
check("medplus profile -> profile task",
      _kp2 is not None and _kp2.get("intent") == "profile"
      and _kp2.get("names") == ["MEDPLUS"],
      repr((_kp2 or {}).get("names")))
check("spar full profile -> profile task",
      (detect_task("spar full profile") or {}).get("intent") == "profile")
check("spar everything (reverse order) -> profile",
      (detect_task("spar everything") or {}).get("intent") == "profile")
check("medplus everything -> profile",
      (detect_task("medplus everything") or {}).get("intent") == "profile")
check("get the full profile for medplus -> profile",
      (detect_task("get me the full profile for medplus") or {}).get("intent")
      == "profile")
check("bare 'get me everything' stays a search (no merchant)",
      detect_task("get me everything") is None)

# Typo tolerance: within-one-edit key merchant roots
check("typo MEDPLUZ -> MEDPLUS root", _km_matches("MEDPLUZ") == ["MEDPLUS"])
check("typo ADIDE -> ADDIDE root", _km_matches("ADIDE") == ["ADDIDE"])
check("typo CASCADE -> CASCADES root", _km_matches("CASCADE") == ["CASCADES"])
check("no-typo SPARE PARTS never hits SPAR", not _km_matches("SPARE PARTS"))
check("no-typo short root NNPC not fuzzy-hijacked", not _km_matches("NPC"),
      repr(_km_matches("NPC")))
check("multi-word root JUST CHIPS exact-only",
      _km_matches("JUST CHIPS") == ["JUST CHIPS"])

# Typo'd key-merchant requests still route AND get the canonical name
_kp3 = detect_task("medpluz emails")
check("medpluz emails -> email task with canonical MEDPLUS",
      _kp3 is not None and _kp3.get("intent") == "email"
      and _kp3.get("names") == ["MEDPLUS"]
      and _kp3.get("key_merchants") == ["MEDPLUS"],
      repr((_kp3 or {}).get("names")))
_kp4 = detect_task("adide addresses")
check("adide addresses -> address task with canonical ADDIDE",
      _kp4 is not None and _kp4.get("intent") == "address"
      and _kp4.get("names") == ["ADDIDE"]
      and _kp4.get("key_merchants") == ["ADDIDE"],
      repr((_kp4 or {}).get("names")))
# Typo-tolerant segment gate: "all adide stores in lagos" is a collection
_kp5 = detect_task("all adide stores in lagos")
check("all-adide-stores-in-lagos -> segment ADDIDE (typo gate)",
      _kp5 is not None and _kp5.get("intent") == "segment"
      and _kp5.get("segment") == "ADDIDE",
      repr((_kp5 or {}).get("segment")))
# The matched root rides on the task descriptor for the Rule Engine badge
check("key_merchants recorded on task descriptor",
      (_kp3 or {}).get("key_merchants") == ["MEDPLUS"])
check("analyze() exposes key_merchants",
      analyze("medpluz emails").get("key_merchants") == ["MEDPLUS"])

# ── New key-merchant roots: LAGOON WATERS + CASCADES LUXE/LUXURY variants ──
check("LAGOON WATERS is a key root", _match_key_merchant("LAGOON WATERS LTD"))
check("LAGOON WATERS NNPC row is a key root",
      _match_key_merchant("LAGOON WATERS LTD - NNPC"))
check("CASCADES LUXE exact root", _km_matches("CASCADES LUXE") == ["CASCADES LUXE"])
check("CASCADES LUXURY family root",
      _km_matches("CASCADES LUXURY LIMITED") == ["CASCADES LUXURY"])
check("CASCADES LUXE variant wins over bare CASCADES",
      _km_matches("CASCADES LUXE LIMITED")[0] == "CASCADES LUXE",
      repr(_km_matches("CASCADES LUXE LIMITED")))
check("bare CASCADES still a key root", _match_key_merchant("CASCADES LUXURY LIMITED"))
check("LAGOON WATERS typo variant routes", _km_matches("LAGOON WATERS") == ["LAGOON WATERS"])

# ── New key-merchant roots: BOKKU MART / ORIENT AFRICA / SHOPRITE /
#    KONGAPAY / GENESIS FOODS (DB-grounded families, same typo tolerance) ──
check("BOKKU MART is a key root", _km_matches("BOKKU MART") == ["BOKKU MART"])
check("BOKKU MART branch row is a key root",
      _km_matches("BOKKU MART- ILAJE AJAH") == ["BOKKU MART"])
check("ORIENT AFRICA NNPC row is a key root",
      _km_matches("ORIENT AFRICA COMPANY LTD-NNPC MEGA STATION")
      == ["ORIENT AFRICA"])
check("SHOPRITE is a key root", _km_matches("SHOPRITE LIMITED") == ["SHOPRITE"])
check("KONGAPAY is a key root", _km_matches("KONGAPAY MERCHANT POS") == ["KONGAPAY"])
check("GENESIS FOODS is a key root",
      _km_matches("GENESIS FOODS ABUJA APO") == ["GENESIS FOODS"])
# Typo tolerance works on the new single-word roots (5+ chars, one edit).
check("typo SHOPRIT -> SHOPRITE root", _km_matches("SHOPRIT") == ["SHOPRITE"])
check("typo KONGOPAY -> KONGAPAY root", _km_matches("KONGOPAY") == ["KONGAPAY"])
# New roots route bare field requests like the other key merchants.
check("bokku mart address -> address task",
      (detect_task("bokku mart address") or {}).get("intent") == "address")
check("orient africa emails -> email task",
      (detect_task("orient africa emails") or {}).get("intent") == "email")
check("shoprite phone number -> phone task",
      (detect_task("shoprite phone number") or {}).get("intent") == "phone")
check("all bokku mart stores -> segment BOKKU MART",
      (detect_task("all bokku mart stores") or {}).get("segment") == "BOKKU MART")
# Platform/bank aggregates are deliberately NOT key roots.
check("ZINTERNET is not a key root", not _match_key_merchant("ZINTERNET NIGERIA LIMITED"))
check("TRACTION APPS is not a key root", not _match_key_merchant("TRACTION APPS LIMITED"))

# LAGOON WATERS now routes bare field requests like the other key merchants
_kp6 = detect_task("lagoon waters address")
check("lagoon waters address -> address task",
      _kp6 is not None and _kp6.get("intent") == "address"
      and _kp6.get("names") == ["LAGOON WATERS"],
      repr((_kp6 or {}).get("names")))
check("lagoon waters emails -> email task",
      (detect_task("lagoon waters emails") or {}).get("intent") == "email")
check("cascades luxe profile -> profile task",
      (detect_task("cascades luxe profile") or {}).get("intent") == "profile")
check("all lagoon waters stations -> segment LAGOON WATERS",
      (detect_task("all lagoon waters stations") or {}).get("segment")
      == "LAGOON WATERS")
# Guards: genuinely non-key names with field words STAY normal searches
check("PALM GROVE ADDRESS stays a search (non-key guard)",
      detect_task("palm grove address") is None)
check("SUNRISE PLAZA bank stays a search (non-key guard)",
      detect_task("sunrise plaza bank") is None)

# ── v2 intent parser: confidence, new intents, filters, anchored names
print("\n[4c] v2 intent parser (confidence / new intents / filters)")

# Confidence: strong phrases score high, generic words low, resolve 0
c1 = detect_task("get the static account for MX183544")
check("static-account task has confidence", c1 is not None and 80 <= c1.get("confidence", 0) <= 100,
      repr((c1 or {}).get("confidence")))
check("plain name has no confidence leak",
      detect_task("LAGOON WATERS") is None)

# New intents: count / duplicates / summary
cnt = detect_task("how many nnpc merchants are there")
check("count intent detected", (cnt or {}).get("intent") == "count",
      repr((cnt or {}).get("intent")))
if cnt:
    rcnt = execute_task(cnt)
    check("count produced a metric", len(rcnt["rows"]) >= 1 and "count" in rcnt["rows"][0],
          repr(rcnt["rows"]))
    check("count segment extracted", cnt.get("segment") == "NNPC",
          repr(cnt.get("segment")))
    check("count never leaks names", cnt.get("names") == [])
check("count of monte cristo is NOT a task",
      detect_task("count of monte cristo") is None)

dup = detect_task("find duplicate merchants in the NNPC file")
check("duplicates intent detected", (dup or {}).get("intent") == "duplicates",
      repr((dup or {}).get("intent")))
if dup:
    rdup = execute_task(dup)
    check("duplicates found clusters", len(rdup["rows"]) >= 1, f"{len(rdup['rows'])}")

smm = detect_task("summarize the NNPC PARAMETER FILE BATCH sheet")
check("summary intent detected", (smm or {}).get("intent") == "summary",
      repr((smm or {}).get("intent")))
if smm:
    rsmm = execute_task(smm)
    check("summary has metrics", len(rsmm["rows"]) >= 5, f"{len(rsmm['rows'])}")

# Filters & limits
f1 = detect_task("get me all the addresses of all nnpc merchants in lagos")
check("state filter extracted", (f1 or {}).get("params", {}).get("state") == "LAGOS",
      repr((f1 or {}).get("params")))
check("state word removed from segment", (f1 or {}).get("segment") == "NNPC",
      repr((f1 or {}).get("segment")))
if f1:
    rf1 = execute_task(f1)
    check("state filter narrows results", len(rf1["rows"]) < 996,
          f"{len(rf1['rows'])}")
f2 = detect_task("get me all the emails of all nnpc merchants with email, top 20")
check("limit filter extracted", (f2 or {}).get("params", {}).get("limit") == 20,
      repr((f2 or {}).get("params")))
check("limit words removed from segment", (f2 or {}).get("segment") == "NNPC",
      repr((f2 or {}).get("segment")))
check("presence filter extracted", "email" in (f2 or {}).get("params", {}).get("has", []),
      repr((f2 or {}).get("params")))
if f2:
    rf2 = execute_task(f2)
    check("limit applied", len(rf2["rows"]) == 20, f"{len(rf2['rows'])}")

# Anchored name extraction keeps full merchant names with field words
check("anchored name keeps BANK", extract_names("get me the profile of FIRST BANK") == ["FIRST BANK"],
      repr(extract_names("get me the profile of FIRST BANK")))
check("anchored name LAGOON WATERS",
      extract_names("get me all the information on LAGOON WATERS") == ["LAGOON WATERS"])

# Disambiguation: phone-number-of is phone, not count
p1 = detect_task("get me the phone number of MX141692")
check("phone-number-of resolves to phone", (p1 or {}).get("intent") == "phone",
      repr((p1 or {}).get("intent")))

# Multi-line paste with a trailing instruction must not leak a bogus name
# ("get the emails for these" -> 'THESE'): the instruction line is skipped.
multi = detect_task("LAGOON WATERS\nARTEE INDUSTRIES\nget the emails for these")
check("multi-line paste leaks no instruction name",
      multi is None or "THESE" not in (multi.get("names") or []),
      repr((multi or {}).get("names")))

# Identifier-scoped duplicates must not run GLOBAL duplicate detection.
dup_id = detect_task("find duplicates for MX183639")
if dup_id:
    rdup_id = execute_task(dup_id)
    check("duplicates scoped to identifier rows",
          len(rdup_id["rows"]) <= len(rdup_id.get("rows", [])) and
          all(r.get("rows", 0) >= 1 for r in rdup_id["rows"]),
          f"{len(rdup_id['rows'])} clusters")

# analyze() debug breakdown
an = analyze("get me all the emails of all nnpc merchants with email, top 20")
check("analyze returns is_task", an["is_task"] is True)
check("analyze primary matches task", an["primary"] == an["task"]["intent"],
      f"{an['primary']} vs {an['task']['intent']}")
check("analyze reports confidence", 0 < an["confidence"] <= 100, f"{an['confidence']}")
check("analyze lists intent breakdown", any(i["intent"] == "email" for i in an["intents"]),
      repr(an["intents"]))
check("analyze exposes params", an["params"]["limit"] == 20, repr(an["params"]))
check("extract_params parses state", extract_params("in lagos with email top 5") ==
      {"state": "LAGOS", "has": ["email"], "missing": [], "limit": 5},
      repr(extract_params("in lagos with email top 5")))
check("extract_params parses missing fields",
      extract_params("which nnpc stations have no email or phone") ==
      {"state": "", "has": [], "missing": ["email", "phone"], "limit": None},
      repr(extract_params("which nnpc stations have no email or phone")))

# ── New intents: live pipelines (field extraction + analytical) ──────────
print("\n[4n] new intents (live pipelines)")
for _q, _int, _col in [
    ("get me the bank for 2ISW916B", "bank", "Bank"),
    ("what's the account name for MX141692", "account_name", "Account Name"),
    ("get the account number for 2ISW916B", "account_number", "Account Number"),
    ("who is the contact person at LAGOON WATERS", "contact", "Contact"),
    ("when was 2ISW916B onboarded", "onboarded", "Onboarded"),
    ("what state is 2ISW916B in", "state", "State"),
    ("which file is MX141692 in", "source", "Source"),
]:
    _t = detect_task(_q)
    check(f"{_int} task detected",
          _t is not None and _t.get("intent") == _int,
          repr((_t or {}).get("intent")))
    if _t:
        _r = execute_task(_t)
        check(f"{_int} pipeline ran", _r.get("intent") == _int, _r.get("intent"))
        check(f"{_int} returns {_col} + rows",
              _col in _r.get("columns", []) and len(_r.get("rows", [])) >= 1,
              repr((_r.get("columns"), len(_r.get("rows", [])))))

# ── Confusable identifiers (0↔O, 1↔I, 2↔Z, 5↔S, 8↔B) ───────────────────
# WSV VENTURES is stored with TID 2103O265 (letter O). The digit-0 spelling
# 21030265 must resolve the same row through the task engine — the DB is the
# ground truth, so the confusable variant only matches because the registry
# stores that form.
print("\n[4o] confusable identifier resolution (0/O etc.)")
from merchant_intelligence.tasks.db import _connect, resolve_any, resolve_mx

_cfg_conn = _connect()
_r_conf = resolve_any(_cfg_conn, ["21030265"])
check("resolve_any maps digit-0 TID to a row",
      "21030265" in _r_conf, repr(list(_r_conf)))
if _r_conf.get("21030265"):
    check("confusable row is WSV VENTURES",
          "WSV" in str(_r_conf["21030265"].get("merchant_name", "")).upper(),
          repr(_r_conf["21030265"].get("merchant_name")))
    check("confusable row keeps the stored letter-O TID",
          _r_conf["21030265"].get("tid") == "2103O265",
          repr(_r_conf["21030265"].get("tid")))
_r_conf2 = resolve_mx(_cfg_conn, ["21030265"])
check("resolve_mx maps digit-0 TID to its MX row",
      bool(_r_conf2), repr(list(_r_conf2)))
check("confusable MX row carries MX183645",
      any(str(r.get("mxcode")) == "MX183645" for r in _r_conf2.values()),
      repr([r.get("mxcode") for r in _r_conf2.values()]))
_cfg_conn.close()
# The exact letter-O spelling still resolves through the pipeline unchanged.
_t_conf = detect_task("21030265 please assist with this dealer details")
check("digit-0 TID parses as identifier",
      _t_conf is not None and "21030265" in (_t_conf.get("identifiers") or {}).get("tid", []),
      repr((_t_conf or {}).get("identifiers")))
if _t_conf:
    _r_conf3 = execute_task(_t_conf)
    check("confusable task resolves WSV VENTURES",
          any("WSV" in str(r.get("merchant") or "").upper()
              for r in _r_conf3.get("rows", [])),
          repr([r.get("merchant") for r in _r_conf3.get("rows", [])]))
# subsumes them — its pipeline returns the Alias / Beneficiary / Payable Code
# columns directly).
_t_pay = detect_task("get the payable code for MX141692 but not the static account")
check("payable pipeline via negation",
      _t_pay is not None and _t_pay.get("intent") == "payable",
      repr((_t_pay or {}).get("intent")))
if _t_pay:
    _r_pay = execute_task(_t_pay)
    check("payable returns Payable Code column + rows",
          "Payable Code" in _r_pay.get("columns", [])
          and len(_r_pay.get("rows", [])) >= 1,
          repr((_r_pay.get("columns"), len(_r_pay.get("rows", [])))))
_t_alias = detect_task("list the aliases for MX141692 but not the static account")
check("alias pipeline via negation",
      _t_alias is not None and _t_alias.get("intent") == "alias",
      repr((_t_alias or {}).get("intent")))
if _t_alias:
    _r_alias = execute_task(_t_alias)
    check("alias returns Alias column + rows",
          "Alias" in _r_alias.get("columns", []) and len(_r_alias.get("rows", [])) >= 1,
          repr((_r_alias.get("columns"), len(_r_alias.get("rows", [])))))
_t_bnf = detect_task("get the beneficiary for MX141692 but not the static account")
check("beneficiary pipeline via negation",
      _t_bnf is not None and _t_bnf.get("intent") == "beneficiary",
      repr((_t_bnf or {}).get("intent")))
if _t_bnf:
    _r_bnf = execute_task(_t_bnf)
    check("beneficiary returns rows",
          len(_r_bnf.get("rows", [])) >= 1,
          f"{len(_r_bnf.get('rows', []))}")

# Verify: found vs not-found
_t_v = detect_task("is 2ISW916B in the registry")
check("verify task detected", _t_v is not None and _t_v.get("intent") == "verify",
      repr((_t_v or {}).get("intent")))
if _t_v:
    _r_v = execute_task(_t_v)
    check("verify found the TID",
          len(_r_v.get("rows", [])) >= 1 and _r_v["rows"][0].get("found") == "Yes",
          repr(_r_v.get("rows")))
_t_v2 = detect_task("is ZZQQXXYY in the registry")
if _t_v2:
    _r_v2 = execute_task(_t_v2)
    check("verify reports not-found for unknown",
          len(_r_v2.get("rows", [])) >= 1 and _r_v2["rows"][0].get("found") == "No",
          repr(_r_v2.get("rows")))

# Related: every record sharing an identifier
_t_rel = detect_task("who else is linked to MX141692")
check("related task detected", _t_rel is not None and _t_rel.get("intent") == "related",
      repr((_t_rel or {}).get("intent")))
if _t_rel:
    _r_rel = execute_task(_t_rel)
    check("related returns linked records", len(_r_rel.get("rows", [])) >= 1,
          f"{len(_r_rel.get('rows', []))}")

# Formerly: name variants for JUST CHIPS
_t_fm = detect_task("what was just chips formerly called")
check("formerly task detected", _t_fm is not None and _t_fm.get("intent") == "formerly",
      repr((_t_fm or {}).get("intent")))
if _t_fm:
    _r_fm = execute_task(_t_fm)
    check("formerly returns name variants", len(_r_fm.get("rows", [])) >= 1,
          repr([(r.get("variant"), r.get("source")) for r in _r_fm.get("rows", [])][:5]))

# Compare: side-by-side table
_t_cmp = detect_task("compare LAGOON WATERS vs ARTEE INDUSTRIES")
check("compare task detected", _t_cmp is not None and _t_cmp.get("intent") == "compare",
      repr((_t_cmp or {}).get("intent")))
if _t_cmp:
    _r_cmp = execute_task(_t_cmp)
    check("compare side-by-side table",
          len(_r_cmp.get("rows", [])) >= 5
          and _r_cmp.get("columns") == ["Field", "Entity A", "Entity B"],
          repr((_r_cmp.get("columns"), len(_r_cmp.get("rows", [])))))
    check("compare rows carry entity values",
          all(r.get("field") for r in _r_cmp["rows"][:3]) and
          any(r.get("entity_a") or r.get("entity_b") for r in _r_cmp["rows"]),
          repr(_r_cmp["rows"][:2]))

# Coverage: NNPC rows missing email
_t_cov = detect_task("which nnpc stations have no email")
check("coverage task detected", _t_cov is not None and _t_cov.get("intent") == "coverage",
      repr((_t_cov or {}).get("intent")))
if _t_cov:
    _r_cov = execute_task(_t_cov)
    _cov_first = _r_cov["rows"][0].get("email") if _r_cov.get("rows") else None
    check("coverage rows all missing email",
          len(_r_cov.get("rows", [])) >= 1
          and all(not r.get("email") for r in _r_cov.get("rows", [])),
          f"{len(_r_cov.get('rows', []))} rows, first email={_cov_first!r}")

# Top N ranking + per-state
_t_top = detect_task("top 10 banks in the NNPC file")
check("top task detected", _t_top is not None and _t_top.get("intent") == "top",
      repr((_t_top or {}).get("intent")))
if _t_top:
    _r_top = execute_task(_t_top)
    check("top returns ranked groups",
          len(_r_top.get("rows", [])) >= 1 and "Count" in _r_top.get("columns", []),
          repr((_r_top.get("columns"), len(_r_top.get("rows", [])))))
_t_st = detect_task("how many merchants per state")
check("per-state task detected", _t_st is not None and _t_st.get("intent") == "top",
      repr((_t_st or {}).get("intent")))
if _t_st:
    _r_st = execute_task(_t_st)
    check("per-state returns grouped rows", len(_r_st.get("rows", [])) >= 5,
          f"{len(_r_st.get('rows', []))}")

# ── Pasted request templates ("Please retrieve this merchant's MXCODE…")
print("\n[4d] request templates (static account phrasing)")
tpl = ("Please retrieve this merchant's MXCODE: MX183639. Then use the MXCODE "
       "to obtain the merchant's static account details and beneficiary name "
       "from the Static Account Manager.")
t_tpl = detect_task(tpl)
check("template request is a task", t_tpl is not None)
check("template intent is static_account", (t_tpl or {}).get("intent") == "static_account",
      repr((t_tpl or {}).get("intent")))
check("template MX parsed despite punctuation",
      "MX183639" in ((t_tpl or {}).get("identifiers") or {}).get("mxcode", []),
      repr((t_tpl or {}).get("identifiers")))
check("change_details does not ride along",
      "change_details" not in ((t_tpl or {}).get("intents") or []),
      repr((t_tpl or {}).get("intents")))

# Punctuation stripping must not corrupt a real email identifier
eml = parse_identifiers("email them at a@b.com. thanks")
check("email with trailing period still parses",
      "a@b.com" in eml.get("email", []), repr(eml.get("email")))

# A genuine compound request keeps BOTH intents
both = detect_task("get the static account and the change details of MX183639")
check("static + change-details compound keeps both",
      both is not None and set(both.get("intents", [])) >= {"static_account", "change_details"},
      repr((both or {}).get("intents")))
if t_tpl:
    r_tpl = execute_task(t_tpl)
    check("template resolves static account", len(r_tpl["rows"]) >= 1 and
          any(row.get("static_acc_no") for row in r_tpl["rows"]),
          f"{len(r_tpl['rows'])} rows")

# Same template with a TID — the trailing period must not break the parse
ntpl = ("Please retrieve this merchant's MXCODE: 2103O338. Then use the MXCODE "
        "to obtain the merchant's static account details and beneficiary name "
        "from the Static Account Manager.")
t_tid = detect_task(ntpl)
check("template TID parsed despite punctuation",
      "2103O338" in ((t_tid or {}).get("identifiers") or {}).get("tid", []),
      repr((t_tid or {}).get("identifiers")))

# Template with a merchant NAME — clean extraction, no template-word garbage
nt2 = ("Please retrieve this merchant's MXCODE: LAGOON WATERS LTD. Then use the "
       "MXCODE to obtain the merchant's static account details and beneficiary "
       "name from the Static Account Manager.")
t_nm = detect_task(nt2)
check("template name cleanly extracted", (t_nm or {}).get("names") == ["LAGOON WATERS LTD"],
      repr((t_nm or {}).get("names")))
if t_nm:
    r_nm = execute_task(t_nm)
    check("template name resolves static account", len(r_nm["rows"]) >= 1 and
          any(row.get("static_acc_no") for row in r_nm["rows"]),
          f"{len(r_nm['rows'])} rows")

# Bare template with NO merchant — helpful message, not a garbage search
bt = ("Please retrieve this merchant's MXCODE. Then use the MXCODE to obtain "
      "the merchant's static account details and beneficiary name from the "
      "Static Account Manager.")
t_bt = detect_task(bt)
check("bare template is still a task", t_bt is not None)
if t_bt:
    r_bt = execute_task(t_bt)
    check("bare template says no merchant found",
          "No merchant identifier or name" in r_bt["summary"], r_bt["summary"])
    check("bare template leaks no names", (t_bt or {}).get("names") == [],
          repr((t_bt or {}).get("names")))

# ── API smoke (endpoints) ────────────────────────────────────────────────
print("\n[5] /api/task endpoints (live server)")
import json
import urllib.error
import urllib.request


def api_post(path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return None, str(e)


status, body = api_post("/api/task", {"text": example})
check("POST /api/task returns 200", status == 200, f"{status} {body}")
check("/api/task says is_task", isinstance(body, dict) and body.get("is_task") is True,
      repr(body)[:160])
if isinstance(body, dict) and body.get("rows"):
    check("/api/task rows carry static accounts",
          any(r.get("static_acc_no") for r in body["rows"]))

status2, body2 = api_post("/api/task", {"text": "LAGOON WATERS"})
check("plain search not a task at API",
      status2 == 200 and body2.get("is_task") is False, repr(body2)[:120])

# ── Intent config API (Rule Engine tuning UI) ─────────────────────────────
print("\n[5b] /api/intents endpoints (live server)")


def api_get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return None, str(e)


def api_put(path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return None, str(e)


s_int, cfg_int = api_get("/api/intents")
check("GET /api/intents returns 200", s_int == 200, str(s_int))
check("GET returns intents dict with email",
      isinstance(cfg_int.get("intents"), dict) and "email" in (cfg_int.get("intents") or {}),
      repr(cfg_int)[:160])
check("GET returns source path", bool(cfg_int.get("source")), cfg_int.get("source", ""))
check("GET lists registered pipelines",
      isinstance(cfg_int.get("pipelines"), list) and "email" in (cfg_int.get("pipelines") or []))
check("GET returns defaults for restore",
      isinstance(cfg_int.get("defaults"), dict) and "email" in (cfg_int.get("defaults") or {}))

# Invalid regex must be rejected with a 400 and a useful message
s_bad, b_bad = api_put("/api/intents", {
    "intent": "email",
    "patterns": [{"pattern": "[unclosed", "weight": 5}],
    "keywords": ["email"],
})
check("PUT rejects invalid regex", s_bad == 400, f"{s_bad} {b_bad}")

# Unknown intent -> 404
s_unk, b_unk = api_put("/api/intents", {
    "intent": "not_a_real_intent",
    "patterns": [{"pattern": "x", "weight": 5}],
    "keywords": ["x"],
})
check("PUT rejects unknown intent", s_unk == 404, f"{s_unk} {b_unk}")

# Round-trip: add a test pattern, verify the API hot-reloaded it (its process
# never restarted), verify the file persisted for a fresh import, then restore.
from merchant_intelligence.tasks import vocab as _vocab
# Snapshot `orig` from the FILE, not the live-process response: the app may
# have been started before an out-of-band config change (e.g. the enrichment
# CLI merge), so its in-memory copy can be stale — restoring from it would
# clobber newer patterns (mail/post) and break the defaults parity check.
_vocab.reload_intents()
orig = _vocab._INTENT_CONFIG["intents"]["email"]
# Patterns match against the LOWERCASED request (the engine lowercases the
# text before scoring), so test patterns must be lowercase too.
extra = {"pattern": "\\bget-only-my-code\\b", "weight": 9}
s_upd, b_upd = api_put("/api/intents", {
    "intent": "email",
    "patterns": orig["patterns"] + [extra],
    "keywords": orig["keywords"],
})
check("PUT saves intent change", s_upd == 200 and b_upd.get("ok"), f"{s_upd} {b_upd}")

# The running API process must now route the test phrase WITHOUT a restart.
_an_status, _an = api_post("/api/task/analyze", {"text": "get-only-my-code"})
check("API hot-reloaded new pattern (no restart)",
      _an_status == 200 and isinstance(_an, dict) and _an.get("primary") == "email",
      f"{_an_status} {repr(_an)[:160]}")

# A fresh reload in THIS process sees the persisted file too.
_vocab.reload_intents()
from merchant_intelligence.tasks import detect_intent
_diag = ("file=" + str([p["pattern"] for p in _vocab._INTENT_CONFIG["intents"]["email"]["patterns"]])
         + " compiled=" + str([str(p.pattern) for p, _ in _vocab.COMPILED_INTENT_PATTERNS["email"]]))
check("file persisted: new pattern fires on reload",
      detect_intent("get-only-my-code") == "email", _diag)

# Restore the original patterns and verify both sides go back to normal.
s_rest, b_rest = api_put("/api/intents", {
    "intent": "email",
    "patterns": orig["patterns"],
    "keywords": orig["keywords"],
})
check("PUT restores original config", s_rest == 200 and b_rest.get("ok"), f"{s_rest} {b_rest}")
_an2_status, _an2 = api_post("/api/task/analyze", {"text": "GET-ONLY-MY-CODE"})
check("restore live: test pattern no longer fires",
      _an2_status == 200 and _an2.get("primary") != "email",
      f"{_an2_status} {repr(_an2)[:120]}")
_vocab.reload_intents()
check("restore verified after reload",
      detect_intent("get-only-my-code") != "email",
      detect_intent("get-only-my-code"))
check("intents back to defaults after round-trip",
      _vocab.INTENT_PATTERNS == _vocab._DEFAULT_INTENT_PATTERNS)

# ── Clarification API round-trip (ambiguous -> ask -> pick -> run) ───────
print("\n[5c] /api/task clarification round-trip (live server)")
_sc_status, _sc = api_post("/api/task", {"text": "get account details for medplus"})
check("ambiguous request returns needs_clarification",
      _sc_status == 200 and isinstance(_sc, dict)
      and _sc.get("needs_clarification") is True, f"{_sc_status} {repr(_sc)[:160]}")
if isinstance(_sc, dict) and _sc.get("needs_clarification"):
    check("clarification response carries options",
          len(_sc.get("options", [])) >= 2
          and all(o.get("intent") and o.get("label") for o in _sc["options"]),
          repr(_sc.get("options")))
    check("clarification response carries the question",
          bool(_sc.get("question")), _sc.get("question", ""))
    check("no pipeline ran yet (no rows)",
          _sc.get("rows") is None and "pipeline" not in _sc,
          repr(list(_sc)[:8]))
    # Pick an option -> force that intent -> the pipeline actually runs.
    _pick = _sc["options"][0]["intent"]
    _sp_status, _sp = api_post("/api/task", {
        "text": "get account details for medplus", "intent": _pick})
    check("picking an option runs the chosen pipeline",
          _sp_status == 200 and isinstance(_sp, dict)
          and _sp.get("is_task") is True
          and _sp.get("detected", {}).get("intent") == _pick
          and _sp.get("needs_clarification") is not True,
          f"{_sp_status} {repr(_sp)[:160]}")

# Decisive request never asks at the API either.
_sc2_status, _sc2 = api_post("/api/task", {"text": "get the static account for MX183544"})
check("decisive request runs directly (no clarification)",
      _sc2_status == 200 and isinstance(_sc2, dict)
      and _sc2.get("needs_clarification") is not True
      and _sc2.get("is_task") is True, f"{_sc2_status} {repr(_sc2)[:120]}")

# ── Calibration API (stats + reset) ──────────────────────────────────────
print("\n[5d] /api/calibration endpoints (live server)")
_s_cal, _b_cal = api_get("/api/calibration")
check("GET /api/calibration returns 200", _s_cal == 200, str(_s_cal))
check("calibration payload has stats + fit + params",
      isinstance(_b_cal, dict) and "stats" in _b_cal and "fit" in _b_cal
      and "params" in _b_cal, repr(_b_cal)[:160])
if isinstance(_b_cal, dict):
    check("calibration stats have decisions + bands",
          "decisions" in _b_cal["stats"] and isinstance(_b_cal["stats"].get("bands"), list),
          repr(_b_cal["stats"])[:160])
    check("calibration params carry ask threshold",
          isinstance(_b_cal["params"].get("ask_threshold"), int),
          repr(_b_cal["params"]))
    # The race-window fit is a first-class threshold now — pin the contract
    # so a stale server (ask-only fit) fails instead of silently rendering
    # the default gap forever in the Rule Engine panel.
    check("calibration params carry the race-window threshold",
          isinstance(_b_cal["params"].get("gap_threshold"), (int, float))
          and "gap_active" in _b_cal["params"], repr(_b_cal["params"]))
    check("calibration stats expose race-gap bands",
          isinstance(_b_cal["stats"].get("gap_bands"), list)
          and "race_decisions" in _b_cal["stats"],
          repr(_b_cal["stats"])[:160])
    # The UI reads sources.accept / sources.override — pin the contract so a
    # stale server (old clarify-only keys) fails this test instead of silently
    # rendering 0/0 in the panel.
    check("calibration sources expose accept + override buckets",
          isinstance(_b_cal["stats"].get("sources"), dict)
          and "accept" in _b_cal["stats"]["sources"]
          and "override" in _b_cal["stats"]["sources"],
          repr(_b_cal["stats"].get("sources")))

# Running a decisive task records an auto decision.
_s_before = _b_cal["stats"]["decisions"] if isinstance(_b_cal, dict) else 0
_s_run, _b_run = api_post("/api/task", {"text": "get me all the information on medplus"})
_s_after, _b_after = api_get("/api/calibration")
check("auto-routed task logs a calibration decision",
      _s_run == 200 and _s_after == 200
      and _b_after["stats"]["decisions"] == _s_before + 1,
      f"before={_s_before} after={_b_after['stats']['decisions']}")

# The reset endpoint is intentionally NOT called here — it wipes the REAL
# data/request_log.jsonl the live server writes to (destructive). Reset
# semantics are covered destructively in [4l] against a temp file.

# ── Saved-interpretations API (learn / auto-pick / forget) ──────────────
print("\n[5e] /api/preferences + remember-my-choice (live server)")
# Non-destructive by design: a DISTINCTIVE phrase key ("account details
# zztest") is learned, verified, then forgotten — real user preferences are
# never touched. Crash-safe: every response is isinstance-guarded before
# .get so a stale server can never abort the section and skip the cleanup.
def _pref_list(body):
    """preferences[] from an API body, or [] when the body isn't a dict."""
    return (body.get("preferences") if isinstance(body, dict)
            and isinstance(body.get("preferences"), list) else [])

_pref_phrase = "get the account details for MX141692 and zztest"
_pref_key = "account details zztest"
_s_pref0, _b_pref0 = api_get("/api/preferences")
check("GET /api/preferences returns 200", _s_pref0 == 200, str(_s_pref0))
check("preferences payload has count + list",
      isinstance(_b_pref0, dict) and "count" in _b_pref0
      and isinstance(_b_pref0.get("preferences"), list),
      repr(_b_pref0)[:160])

# Learn: POST /api/task with an explicit intent + remember: true.
_s_lrn, _b_lrn = api_post("/api/task", {
    "text": _pref_phrase, "intent": "static_account", "remember": True})
check("remember-pick runs the chosen intent",
      _s_lrn == 200 and isinstance(_b_lrn, dict)
      and _b_lrn.get("is_task") is True
      and _b_lrn.get("detected", {}).get("intent") == "static_account",
      f"{_s_lrn} {repr(_b_lrn)[:160]}")

# The phrase -> intent is now persisted.
_s_pref1, _b_pref1 = api_get("/api/preferences")
_pref_saved = [p for p in _pref_list(_b_pref1) if p.get("key") == _pref_key]
check("preference persisted for the distinctive phrase",
      _s_pref1 == 200 and len(_pref_saved) == 1
      and _pref_saved[0]["intent"] == "static_account",
      repr(_b_pref1)[:200])

# Auto-pick: the same phrase WITHOUT an intent now runs the saved choice
# directly (no clarification card).
_s_ap, _b_ap = api_post("/api/task", {"text": _pref_phrase})
check("saved phrase auto-picks (no card)",
      _s_ap == 200 and isinstance(_b_ap, dict)
      and _b_ap.get("is_task") is True
      and _b_ap.get("used_preference") == "static_account"
      and _b_ap.get("needs_clarification") is not True,
      f"{_s_ap} {repr(_b_ap)[:200]}")

# Forget: remove the saved key (self-cleaning — real prefs untouched).
_s_fgt, _b_fgt = api_post("/api/preferences/forget", {"key": _pref_key})
check("forget removes the saved key",
      _s_fgt == 200 and isinstance(_b_fgt, dict) and _b_fgt.get("removed") is True,
      f"{_s_fgt} {repr(_b_fgt)[:160]}")
_s_pref2, _b_pref2 = api_get("/api/preferences")
check("preference gone after forget",
      all(p.get("key") != _pref_key for p in _pref_list(_b_pref2)),
      repr(_b_pref2)[:200])
# Forgetting a missing key is harmless (not an error).
_s_fgt2, _b_fgt2 = api_post("/api/preferences/forget", {"key": _pref_key})
check("forget missing key is harmless",
      _s_fgt2 == 200 and isinstance(_b_fgt2, dict)
      and _b_fgt2.get("removed") is False,
      f"{_s_fgt2} {repr(_b_fgt2)[:120]}")

# ── Pasted merchant-code name list (regression) ───────────────────────────
# A user pastes "pls help get the merchant code for these merchants from
# parameter file <NAME>" + a line-per-name list. It must resolve all names to
# MX codes. Three failure modes were fixed together:
#   1. idclass trusted dirty DB values ('2' in account_number) as identifiers,
#      which disabled name extraction entirely.
#   2. the instruction-line guard used substring matching, so 'GADGET'
#      (contains 'get') was skipped as an "instruction line".
#   3. 'RELIABLE PHONES AND GADGET' fired the phone intent from inside a name.
print("\n[5f] key-merchant badge payloads (live server)")
# /api/similar and /api/batch rows now carry key_merchants so the Similar/
# Related panel and Batch rows can render the same clickable family badge as
# the Search page. MEDPLUS is a key root -> both payloads must list it.
_s_sim, _b_sim = api_post("/api/similar", {"query": "MEDPLUS"})
check("POST /api/similar returns 200", _s_sim == 200, str(_s_sim))
_sim_hit = next((m for m in (_b_sim.get("similar") or [])
                 if "MEDPLUS" in (m.get("merchant_name") or "").upper()
                 and (m.get("key_merchants") or []) == ["MEDPLUS"]), None)
check("similar rows carry key_merchants badge",
      _sim_hit is not None, repr(_b_sim.get("similar"))[:200])
_s_bat, _b_bat = api_post("/api/batch", {"merchants": ["MEDPLUS PHARMACY"]})
check("POST /api/batch returns 200", _s_bat == 200, str(_s_bat))
_bat_hit = next((r for r in (_b_bat.get("rows") or [])
                 if (r.get("key_merchants") or []) == ["MEDPLUS"]), None)
check("batch rows carry key_merchants badge",
      _bat_hit is not None, repr(_b_bat.get("rows"))[:200])

print("\n[5g] ADDIDE-style request variants (live server)")
# The name+MX paste arrives in many shapes — space/tab/comma pairs,
# numbered/bulleted lists, label-colon rows, names-only, MX-only, single
# merchant, slang-heavy and reversed field words. Every variant must route
# to the static_account task and resolve all 3 MX codes with no not-found.
_ADDIDE_MXS = {"MX156725", "MX156710", "MX156720"}
_request_variants = [
    "ADDIDE ABARANJE\nMX156725\nADDIDE AGUDA\nMX156710\nADDIDE EGBE\nMX156720\nhelp wt d alias, payable and tids for d above merchant",
    "ADDIDE ABARANJE MX156725\nADDIDE AGUDA MX156710\nADDIDE EGBE MX156720\npls get the alias, payable and tids for the above merchants",
    "ADDIDE ABARANJE\tMX156725\nADDIDE AGUDA\tMX156710\nADDIDE EGBE\tMX156720\nplease help with the alias, payable and tids for the above",
    "ADDIDE ABARANJE, MX156725\nADDIDE AGUDA, MX156710\nADDIDE EGBE, MX156720\nget the alias payable and tids for d above merchants",
    "ADDIDE ABARANJE\nADDIDE AGUDA\nADDIDE EGBE\npls get the alias, payable and tids for these merchants",
    "MX156725\nMX156710\nMX156720\npls help get the alias, payable and tids for the above merchant",
    "1. ADDIDE ABARANJE - MX156725\n2. ADDIDE AGUDA - MX156710\n3. ADDIDE EGBE - MX156720\nhelp wt d alias, payable and tids for d above merchants",
    "Merchant: ADDIDE ABARANJE  MX: MX156725\nMerchant: ADDIDE AGUDA  MX: MX156710\nMerchant: ADDIDE EGBE  MX: MX156720\npls get their alias, payable and tids",
    "addide abaranje mx156725\naddide aguda mx156710\naddide egbe mx156720\npls help wt d alias, payable and tids for d above mmerchant",
    "ADDIDE ABARANJE MX156725\nADDIDE AGUDA MX156710\nADDIDE EGBE MX156720\nwhat are the tids, payables and aliases for these merchants",
    "* ADDIDE ABARANJE MX156725\n* ADDIDE AGUDA MX156710\n* ADDIDE EGBE MX156720\npls assist with alias, payable and tids for the above",
]
for _vi, _vtext in enumerate(_request_variants):
    _vs, _vb = api_post("/api/task", {"text": _vtext})
    _vrows = (_vb.get("rows") or []) if isinstance(_vb, dict) else []
    _vmxs = {r.get("mxcode") for r in _vrows if r.get("mxcode")}
    _vnf = [x.get("id") for x in (_vb.get("not_found") or [])] if isinstance(_vb, dict) else []
    check(f"variant {_vi+1}: routes as static_account task",
          _vs == 200 and isinstance(_vb, dict) and _vb.get("is_task") is True
          and _vb.get("intent") == "static_account+tid",
          f"{_vs} {repr(_vb)[:160]}")
    check(f"variant {_vi+1}: resolves all 3 ADDIDE MX codes",
          _ADDIDE_MXS.issubset({str(m).upper() for m in _vmxs}) and not _vnf,
          f"mxs={sorted(_vmxs)} not_found={_vnf}")
# Single-merchant variant resolves BOTH static accounts for MX156725
# (the DB holds two: 5180467849 + 5180849133). Assert both known accounts
# appear rather than an exact row count — a future rebuild could add a
# third, and that should not break the format regression test.
_vs7, _vb7 = api_post("/api/task", {"text": "ADDIDE ABARANJE MX156725 get me the alias and payable code"})
_v7_accs = {r.get("static_acc_no") for r in (_vb7.get("rows") or []) if isinstance(_vb7, dict)}
check("single-merchant variant routes + resolves",
      _vs7 == 200 and isinstance(_vb7, dict) and _vb7.get("is_task") is True
      and {"5180467849", "5180849133"}.issubset(_v7_accs)
      and not (_vb7.get("not_found") or []),
      f"{_vs7} accs={sorted(_v7_accs)}")

print("\n[5h] multi-identifier paste resolves both TIDs (live server)")
# Regression: typing "2ISW2587 2ISW2586" into the search bar used to route
# to plain /api/search, which fuzzy-matched the whole blob against unrelated
# rows (UBTH etc.) and never surfaced the MEDPLUS mentions. The backend now
# OR-searches each identifier and merges only exact/high-confidence hits, so
# BOTH TIDs' records must come back with zero Low-Confidence noise.
_s5h, _b5h = api_post("/api/search", {"query": "2ISW2587 2ISW2586", "limit": 20, "offset": 0})
_5h_rows = (_b5h.get("results") or []) if isinstance(_b5h, dict) else []
check("multi-TID search returns 200", _s5h == 200, str(_s5h))
check("multi-TID search returns both TIDs' records",
      {"2ISW2587", "2ISW2586"}.issubset({str(r.get("tid", "")) for r in _5h_rows})
      and any("MEDPLUS" in (r.get("merchant_name") or "").upper() for r in _5h_rows),
      repr(_b5h)[:200])
check("multi-TID search has no Low-Confidence noise",
      all(r.get("match_type") in ("Exact Match", "High Confidence") for r in _5h_rows),
      repr({r.get("match_type") for r in _5h_rows}))
# The same OR-search powers the Excel export (binary response, so fetch
# it directly instead of through the JSON api_post helper).
_5he_req = urllib.request.Request(
    "http://127.0.0.1:8000/api/search/export",
    data=json.dumps({"query": "2ISW2587 2ISW2586", "limit": 100, "offset": 0}).encode(),
    headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(_5he_req, timeout=60) as _5he_resp:
        _5he_body = _5he_resp.read()
        _5he_ok = _5he_resp.status == 200 and _5he_body[:2] == b"PK"
except Exception as _5he_e:
    _5he_ok = False
    _5he_body = str(_5he_e).encode()
check("multi-TID export returns an xlsx", _5he_ok, repr(_5he_body[:60]))
# A single bare TID still searches normally (is_task false) and returns the
# same merchant — the identifier alone must never route as a task.
_s5hs, _b5hs = api_post("/api/search", {"query": "2ISW2587", "limit": 5, "offset": 0})
check("single TID search still finds the merchant",
      _s5hs == 200 and any("MEDPLUS" in (r.get("merchant_name") or "").upper()
                           for r in (_b5hs.get("results") or [])),
      repr(_b5hs)[:200])

print("\n[13] pasted merchant-code name list")
from merchant_intelligence.idclass import classify as _idclassify

# 1) idclass plausibility floor: leaked fragments are never identifiers,
#    while real len-5+ values still classify by DB membership.
check("idclass floor: '2' is not an identifier",
      _idclassify("2") == [], repr(_idclassify("2")))
check("idclass floor: '0' is not an identifier",
      _idclassify("0") == [], repr(_idclassify("0")))
check("idclass floor: 'DR' is not an identifier",
      _idclassify("DR") == [], repr(_idclassify("DR")))
check("idclass floor: real len-5 MX still classifies",
      "mxcode" in _idclassify("MX3490"), repr(_idclassify("MX3490")))
check("idclass floor: real TID still classifies",
      "tid" in _idclassify("2103O338"), repr(_idclassify("2103O338")))

_NAMELIST = (
    "pls help get the merchant code for these merchants from parameter "
    "file IBRAHIM. BABAZAKI - NNPC\n"
    "ADDIDE IWAYA\nIlukweGbone\nFILMHOUSE CINEMA -LANDMARK\n"
    "ADDIDEOLD OTA\nLOKAL BRODA LTD\nSweb_Maryland Mall\n"
    "Suzab petroleum and oil marketing company Ltd - NNPC\nPICCADILLY SUITES\n"
    "ADDIDE IJEGUN\nRUBELS AND ANGELS RESTAURANT IKEJA\nNOFISAT SALAMI\n"
    "REIZ CONTINENTAL HOTELS LTD\nADDIDE PEDRO\nOlawale Oduola\n"
    "ADDIDE IKORODU\nRUBELS AND ANGELS AJAO ESTATE BRANCH\nADDIDE POWERLINE\n"
    "GAJI TAIWO 2 - NNPC\nEsorae Home Stores\nADDIDE APATA\n"
    "BHEERHUGZ CAFE\nFILMHOUSE CINEMA -SURULERE\n"
    "Reliable Phones And Gadget\nUMAR KIBIYA USMAN (NNPC)\nGAJI TAIWO - NNPC.\n"
    "Doghor Boyo-NNPC\nBOKKU MART ELEKO BEACH\n"
    "RUBELS AND ANGELS SURULERE BRANCH\nRUBELS AND ANGELS AMUWO ODOFIN "
    "BRANCH\nFILMHOUSE CINEMA - CIRCLE MALL\n"
    "ORIENT AFRICA COMPANY LTD-NNPC MEGA STATION\nMM2 SUPERMARKET\n"
    "MARIA LAMBO - NNPC\nBOKKU MART- ILAJE AJAH\nMOHAMMED TANKO - NNPC\n"
    "Cascades Luxury Limited.\nCHIKE NJOKU-NNPC.\nARTEE INDUSTRIES LIMTED\n"
    "Medplus Limited"
)

# 2) the request routes to mxcode ONLY (the 'phones' inside
#    "Reliable Phones And Gadget" is a merchant name, not a request) and the
#    stray '2' in "GAJI TAIWO 2 - NNPC" never becomes an identifier, so all
#    40 merchant names are extracted.
_nl_task = detect_task(_NAMELIST)
check("name list: detected as mxcode task",
      _nl_task is not None and _nl_task.get("intent") == "mxcode",
      repr(_nl_task and _nl_task.get("intent")))
check("name list: phone intent pruned from name list",
      _nl_task is not None and _nl_task.get("intents") == ["mxcode"],
      repr(_nl_task and _nl_task.get("intents")))
check("name list: zero identifier count (no '2' leak)",
      _nl_task is not None and _nl_task.get("identifier_count") == 0,
      repr(_nl_task and _nl_task.get("identifier_count")))
_nl_names = extract_names(_NAMELIST)
check("name list: all 40 names extracted", len(_nl_names) == 40,
      f"{len(_nl_names)} names")
check("name list: 40 names on the task",
      _nl_task is not None and len(_nl_task.get("names") or []) == 40,
      f"{len((_nl_task or {}).get('names') or [])} names")
check("name list: GADGET name kept (whole-word instr guard)",
      "RELIABLE PHONES GADGET" in _nl_names, repr(_nl_names))
check("name list: trailing instruction-line name captured",
      "IBRAHIM BABAZAKI NNPC" in _nl_names, repr(_nl_names))
# The same whole-word rule protects the single-line name search and the
# named-pair parser: 'GADGET' never reads as the instruction verb 'get'.
_np_gadget = parse_named_identifiers("MX141692 GADGET WORLD")
check("parse_named_identifiers: GADGET pair not dropped as instruction",
      len(_np_gadget) == 1 and _np_gadget[0]["id"] == "MX141692",
      repr(_np_gadget))
check("single-line GADGET name stays a normal search",
      detect_task("RELIABLE PHONES AND GADGET") is None)

# 4) end-to-end: the mxcode pipeline resolves every name in the list.
_nl_res = execute_task(_nl_task) if _nl_task else {}
check("name list: executes with MX Code column",
      "MX Code" in (_nl_res.get("columns") or []),
      repr(_nl_res.get("columns")))
_identifiers_in_rows = {str(r.get("identifier")).upper()
                        for r in (_nl_res.get("rows") or [])}
_nl_missing = [n for n in (_nl_task or {}).get("names") or []
                if n.upper() not in _identifiers_in_rows]
check("name list: every extracted name appears in the rows",
      _nl_task is not None and not _nl_missing,
      f"rows={len(_nl_res.get('rows') or [])} missing={_nl_missing[:5]}")
check("name list: no not-found entries",
      not (_nl_res.get("not_found") or []), repr(_nl_res.get("not_found")))

# ── Follow-up context: label-glued identifiers + 'the above merchant' ─────
print("\n[14] label-glued identifiers + referential follow-ups")
# 'MXCODE-MX77826' — the MX code glued to its label must still extract, and
# the request must not fall back to a bogus merchant-name search.
_ids_glued = parse_identifiers(
    "please check the static account report server for the payable and "
    "alias tied to this MXCODE-MX77826")
check("glued MXCODE- identifier splits to MX77826",
      _ids_glued.get("mxcode") == ["MX77826"], repr(_ids_glued.get("mxcode")))
_ids_labeled = parse_identifiers("TID:2103O338 PHONE-08000000000 EMAIL-a@b.com")
check("labeled TID: extracts", "2103O338" in _ids_labeled.get("tid", []),
      repr(_ids_labeled))
check("labeled PHONE- extracts", "08000000000" in _ids_labeled.get("phone", []),
      repr(_ids_labeled))
check("labeled EMAIL- extracts", "a@b.com" in _ids_labeled.get("email", []),
      repr(_ids_labeled))
# A merchant name whose word looks like a label must NOT fabricate an id.
check("NO-LIMIT STORES fabricates nothing",
      not any(parse_identifiers("NO-LIMIT STORES").values()),
      repr(parse_identifiers("NO-LIMIT STORES")))

# 'the above merchant' — referential: flagged, no garbage name/segment, and
# the wording 'from d parameter file' never becomes the segment fragment.
_ref = detect_task("help me get all the tids for the above merchant "
                    "from d parameter file and their addresses")
check("reference request detected as a task", _ref is not None, repr(_ref))
check("reference request flagged references_previous",
      _ref is not None and _ref.get("references_previous") is True,
      repr(_ref and _ref.get("references_previous")))
check("reference wording never becomes a merchant name",
      _ref is not None and not (_ref.get("names") or []),
      repr(_ref and _ref.get("names")))
check("'d parameter file' never becomes the segment",
      _ref is not None and not (_ref.get("segment") or ""),
      repr(_ref and _ref.get("segment")))
check("reference request kept the requested fields",
      _ref is not None and set(_ref.get("segment_fields") or []) >= {"tid", "address"},
      repr(_ref and _ref.get("segment_fields")))
# Without context, the pipeline answers honestly instead of dumping rows.
_ref_res = execute_task(_ref) if _ref else {}
check("no-context reference does not dump a wildcard table",
      not (_ref_res.get("rows") or []),
      f"rows={len(_ref_res.get('rows') or [])} "
      f"summary={(_ref_res.get('summary') or '')[:60]}")

# With remembered context, the same request inherits the previous entities.
tasks.remember_entities(identifiers={"tid": ["2103O338"]}, names=["MEDPLUS"])
_ref2 = detect_task("help me get all the tids for the above merchant "
                     "from d parameter file and their addresses")
_own2 = ((_ref2 or {}).get("identifier_count") or 0) > 0 \
    or bool((_ref2 or {}).get("names")) or bool((_ref2 or {}).get("segment"))
_ref_inherited = bool(_ref2) and tasks.inherit_reference(_ref2) \
    if (_ref2 and _ref2.get("references_previous") and not _own2) else False
check("reference resolves against remembered context",
      _ref_inherited is True, repr(_ref_inherited))
check("inherited request switched to the requested field pipelines",
      _ref_inherited and set(_ref2.get("intents") or []) >= {"tid", "address"},
      repr(_ref2 and _ref2.get("intents")))
check("inherited identifiers present",
      _ref_inherited and "2103O338" in (_ref2.get("identifiers") or {}).get("tid", []),
      repr(_ref2 and _ref2.get("identifiers")))
_ref_res2 = execute_task(_ref2) if (_ref_inherited and _ref2) else {}
check("inherited request executes with rows", bool(_ref_res2.get("rows")),
      f"rows={len(_ref_res2.get('rows') or [])}")
check("inherited request carries TID and Address columns",
      "TID" in (_ref_res2.get("columns") or [])
      and "Address" in (_ref_res2.get("columns") or []),
      repr(_ref_res2.get("columns")))

# The tid intent itself: name-only 'get the tids for MEDPLUS'.
_tid_task = detect_task("get the tids for medplus")
check("name-only tid request routes to tid intent",
      _tid_task is not None and _tid_task.get("intent") == "tid",
      repr(_tid_task and _tid_task.get("intent")))
_tid_res = execute_task(_tid_task) if _tid_task else {}
check("tid pipeline returns rows with a TID column",
      bool(_tid_res.get("rows")) and "TID" in (_tid_res.get("columns") or []),
      f"rows={len(_tid_res.get('rows') or [])} "
      f"cols={(_tid_res.get('columns') or [])[:4]}")

# Remembering an empty context must not clobber the last good one.
tasks.remember_entities()  # no entities -> ignored
check("empty remember does not clobber context",
      "MEDPLUS" in (tasks.last_entities().get("names") or []),
      repr(tasks.last_entities()))

# ── Address requests (feature: pasted addresses -> address-column match) ─
# A request like 'get me the tids for <6 addresses>' used to fuzzy name-search
# each road+city string and return unrelated stores (GREEN ISLAND
# RESTAURANTS for MEDPLUS MARINA, BARAMA ENERGY for PROVIDENCE PLAZA).
# Address-looking names must route to the address column with honest
# NOT FOUND, never fuzzy junk.
print("\n[5i] address requests")
check("address line classified", looks_like_address(
    "BRITISH INTERNATIONAL SCHOOL ROAD, LEKKI, LAGOS"))
check("plaza+plot address classified", looks_like_address(
    "PROVIDENCE PLAZA, PLOT 17 & 18 OLOKONLA, SANGOTEDO LAGOS"))
check("locality-pair address classified (MARINA)", looks_like_address(
    "MEDPLUS MARINA LAGOS ISLAND, LAGOS STATE"))
check("plain merchant name NOT an address", not looks_like_address(
    "MEDPLUS PHARMACY"))
check("family merchant with area NOT an address", not looks_like_address(
    "BOKKU MART- ILAJE AJAH"))
check("gadget store NOT an address", not looks_like_address(
    "RELIABLE PHONES AND GADGET"))
check("industries company NOT an address", not looks_like_address(
    "ARTEE INDUSTRIES LIMITED"))

_addr_text = ("get me the tids for  BRITISH INTERNATIONAL SCHOOL ROAD, "
              "LEKKI, LAGOS\n"
              "PROVIDENCE PLAZA, PLOT 17 & 18 OLOKONLA, SANGOTEDO LAGOS\n"
              "MEDPLUS MARINA LAGOS ISLAND, LAGOS STATE\n"
              "FRESHFORTE, Plot C5, Block 12e, Admiralty Way, Lekki Phase "
              "1, Lagos\n"
              "39 LEKKI ESTATE ROAD WITHIN BELA VISTA ESTATE FREEDOM WAY "
              "LEKKI LAGOS\n"
              "MEDPLUS OASIS CENTER , MOB0LAJI BANK ANTHONY WAY OPPOSITE "
              "THE POLICE COLLEGE ,IKEJA")
_addr_task = detect_task(_addr_text)
check("address paste routes to tid intent",
      _addr_task is not None and _addr_task.get("intent") == "tid",
      repr(_addr_task and _addr_task.get("intent")))
check("address paste flags names_are_addresses",
      _addr_task is not None and _addr_task.get("names_are_addresses") is True,
      repr(_addr_task and _addr_task.get("names_are_addresses")))
check("all 6 address names captured (first line not dropped)",
      _addr_task is not None and len(_addr_task.get("names") or []) == 6,
      repr(_addr_task and _addr_task.get("names")))
_addr_res = execute_task(_addr_task) if _addr_task else {}
_addr_rows = _addr_res.get("rows") or []
check("address request returns rows", bool(_addr_rows),
      f"rows={len(_addr_rows)}")
check("no fuzzy name junk in address results", all(
    r.get("status") == "address_match" for r in _addr_rows),
    repr({r.get("status") for r in _addr_rows}))
_addr_tids = {r.get("tid") for r in _addr_rows}
for _expect in ("2ISW2816", "2ISWI393", "2ISWZ318", "2ISW7793",
                "2ISWM151", "2ISWW054"):
    check(f"address resolves {_expect}", _expect in _addr_tids,
          repr(sorted(_addr_tids)))

# A mixed paste (real merchants + one address-like name) must stay on the
# NAME path so a merchant like 'SWEB MARYLAND MALL' is still name-searched.
_mixed = detect_task("get me the tids for\nBOKKU MART- ILAJE AJAH\n"
                      "MEDPLUS PHARMACY")
check("mixed name/address paste stays a name request",
      _mixed is not None and _mixed.get("names_are_addresses") is False,
      repr(_mixed and _mixed.get("names_are_addresses")))
_mixed_res = execute_task(_mixed) if _mixed else {}
_mixed_merch = {r.get("merchant") for r in (_mixed_res.get("rows") or [])}
check("mixed paste still resolves merchants by name",
      any("BOKKU" in (m or "").upper() for m in _mixed_merch)
      or "MEDPLUS PHARMACY" in _mixed_merch,
      repr(sorted(_mixed_merch)[:6]))

# ── Address pastes never trigger the clarification popup ──────────────────
# Address text is full of words that score OTHER intents ("…BANK ANTHONY
# WAY" -> bank, "…LAGOS STATE" -> state, "…PLOT…" -> address). When the
# user EXPLICITLY asked for a field ("get me the tids"), that request is
# decisive and must run directly — a stray address word racing the field
# intent must NOT produce a "which did you want?" card.
from merchant_intelligence.tasks import suggest_clarification as _suggest_clarify_addr
check("address paste never triggers clarification",
      _suggest_clarify_addr(_addr_text) is None,
      repr(_suggest_clarify_addr(_addr_text)))
check("single bank-word address never triggers clarification",
      _suggest_clarify_addr(
          "get me the tid for MEDPLUS OASIS CENTER , MOB0LAJI BANK "
          "ANTHONY WAY OPPOSITE THE POLICE COLLEGE ,IKEJA") is None,
      repr(_suggest_clarify_addr(
          "get me the tid for MEDPLUS OASIS CENTER , MOB0LAJI BANK "
          "ANTHONY WAY OPPOSITE THE POLICE COLLEGE ,IKEJA")))
check("single state-word address never triggers clarification",
      _suggest_clarify_addr(
          "get me the tid for MEDPLUS MARINA LAGOS ISLAND, LAGOS STATE")
      is None,
      repr(_suggest_clarify_addr(
          "get me the tid for MEDPLUS MARINA LAGOS ISLAND, LAGOS STATE")))
# The Matched Address column (added with the address pipeline) must be part
# of the result so each TID is verifiable against the stored address.
check("address result exposes Matched Address column",
      "Matched Address" in (_addr_res.get("columns") or []),
      repr(_addr_res.get("columns")))
check("address rows carry the stored address",
      all((r.get("address") or "").strip() for r in _addr_rows),
      repr([r.get("address") for r in _addr_rows[:2]]))

print("\n" + "=" * 60)
print(f"  RESULT: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
