"""test_copilot.py — Merchant Copilot (roadmap #4, first slice).

Two layers:
  [1] Hermetic decomposition tests — decompose() never touches the DB and
      runs anywhere (no server, no LLM key, no rebuild).
  [2] Live execution tests — run_copilot() through the running API on
      :8000 (same convention as test_tasks.py's live sections). These
      execute real steps against intelligence.db.

Run:  python tests/test_copilot.py
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import copilot  # noqa: E402
from merchant_intelligence.tasks import MAX_INPUT_CHARS  # noqa: E402

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


# ── 1. Hermetic decomposition ────────────────────────────────────────────
print("\n[1] decompose (hermetic — no DB, no LLM key)")

# A single coherent task stays ONE step (the engine handles clause
# attachments and chained workflows internally).
p = copilot.decompose("get the static account and beneficiary for MX141692",
                      use_llm=False)
check("single task -> 1 step", len(p["steps"]) == 1, repr(p["steps"]))
check("single task source=whole", p["steps"][0]["source"] == "whole",
      repr(p["steps"]))
check("mode deterministic without LLM key",
      p["mode"] == "deterministic" and not copilot.llm_configured(), p["mode"])

# Clause-attached compound ("email for A and phone for B") is ONE task the
# engine already scopes internally — it must NOT decompose into two steps.
p = copilot.decompose("get the email for 2103O338 and the phone for MX141692",
                      use_llm=False)
check("clause-attached compound stays 1 step", len(p["steps"]) == 1,
      repr(p["steps"]))

# A genuinely compound request ("find MEDPLUS then get the tids for the
# above merchant") carries a referential step that NEEDS the earlier step —
# it must decompose into two chained clauses.
p = copilot.decompose("find MEDPLUS then get the tids for the above merchant",
                      use_llm=False)
check("referential compound -> 2 steps", len(p["steps"]) == 2,
      repr(p["steps"]))
check("step 1 is the find clause",
      "MEDPLUS" in p["steps"][0]["text"].upper(), repr(p["steps"][0]))
check("step 2 is the referential clause",
      "above merchant" in p["steps"][1]["text"].lower(),
      repr(p["steps"][1]))
check("both sources clause", all(s["source"] == "clause" for s in p["steps"]),
      repr(p["steps"]))

# Comma + 'then' split too: "find SPAR, then the emails"
p = copilot.decompose("find SPAR, then get the emails for the above merchant",
                      use_llm=False)
check("comma+then splits", len(p["steps"]) == 2, repr(p["steps"]))

# A plain name is not a task and not referential — it collapses to a single
# clause (which executes as a search step).
p = copilot.decompose("LAGOON WATERS", use_llm=False)
check("plain name -> 1 clause step", len(p["steps"]) == 1, repr(p["steps"]))

# Oversized input raises ValueError (same contract as detect_task).
try:
    copilot.decompose("x" * (MAX_INPUT_CHARS + 1), use_llm=False)
    check("oversized input raises ValueError", False)
except ValueError:
    check("oversized input raises ValueError", True)

# LLM proposed steps are validated: a garbage step with no content tokens is
# rejected by _plausible_step even if the (unreachable here) LLM sent it.
# Pronoun references ("for those") map onto the engine's reference marker
# so chained steps resolve against the previous step's output.
check("pronoun reference normalized",
      copilot._normalize_reference("the static account for those")
      == "the static account for the above merchant",
      copilot._normalize_reference("the static account for those"))
check("pronoun kept when step carries ids",
      copilot._normalize_reference("the emails for those MX141692")
      == "the emails for those MX141692",
      copilot._normalize_reference("the emails for those MX141692"))
check("garbage step rejected",
      copilot._plausible_step("then do it") is False)
check("entity-carrying step kept",
      copilot._plausible_step("find MEDPLUS") is True)
check("referential step kept",
      copilot._plausible_step("get the tids for the above merchant") is True)

# ── 2. Live execution through the API ────────────────────────────────────
print("\n[2] /api/copilot (live server)")


import time as _time


def api_post(path, payload):
    """POST with retry on connection errors — the API process warms up its
    first request (module imports), so a cold-start race must not fail the
    suite."""
    last = None
    for _attempt in range(4):
        req = urllib.request.Request(
            "http://127.0.0.1:8000" + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:300]
        except Exception as e:  # noqa: BLE001
            last = e
            _time.sleep(2)
    return None, str(last)


status, body = api_post("/api/copilot", {
    "text": "get the email for MX141692 and the phone for 2103O338",
    "use_llm": False,
})
check("POST /api/copilot returns 200", status == 200, f"{status} {body}")
if status == 200:
    check("ok flag", body.get("ok") is True)
    check("mode deterministic when forced",
          body.get("mode") == "deterministic", body.get("mode"))
    check("single coherent step", len(body.get("steps") or []) == 1,
          repr(len(body.get("steps") or [])))
    check("step kind task", (body.get("steps") or [{}])[0].get("kind") == "task",
          repr((body.get("steps") or [{}])[0].get("kind")))
    check("plan matches steps", len(body.get("plan") or []) == len(body.get("steps") or []))
    check("summary present", bool(body.get("summary")), repr(body.get("summary")))

# Chained compound: step 1 finds the merchant, step 2's "the above merchant"
# resolves against step 1's output (follow-up context).
status, body = api_post("/api/copilot", {
    "text": "find MEDPLUS then get the tids for the above merchant",
    "use_llm": False,
})
check("chained compound returns 200", status == 200, f"{status} {body}")
if status == 200:
    steps = body.get("steps") or []
    check("two steps executed", len(steps) == 2, repr(len(steps)))
    check("step 1 is a search", steps and steps[0].get("kind") == "search",
          repr(steps[0] if steps else None))
    if len(steps) > 1:
        check("step 2 is a task", steps[1].get("kind") == "task",
              repr(steps[1].get("kind")))
        check("step 2 intent is tid", steps[1].get("intent") == "tid",
              repr(steps[1].get("intent")))
        check("step 2 resolved from previous step",
              steps[1].get("context_inherited") is True,
              repr(steps[1].get("context_inherited")))
        check("step 2 found rows", steps[1].get("rows", 0) >= 1,
              repr(steps[1].get("rows")))

# Pronoun-reference chain: step 3's "for those" resolves against the
# earlier steps' output, not a garbage search (roadmap #4 chaining).
status, body = api_post("/api/copilot", {
    "text": "find MEDPLUS then get the tids for the above merchant "
            "then the static account for those",
    "use_llm": False,
})
check("pronoun chain returns 200", status == 200, f"{status} {body}")
if status == 200:
    steps = body.get("steps") or []
    check("pronoun chain -> 3 steps", len(steps) == 3, repr(len(steps)))
    if len(steps) >= 3:
        check("step 3 is a static_account task",
              steps[2].get("kind") == "task"
              and steps[2].get("intent") == "static_account",
              repr(steps[2].get("intent")))
        check("step 3 resolved from previous step",
              steps[2].get("context_inherited") is True,
              repr(steps[2].get("context_inherited")))
        check("step 3 ran the static account pipeline",
              "Static Account Number" in (steps[2].get("columns") or []),
              repr(steps[2].get("columns")))

# Empty text -> 400.
status, body = api_post("/api/copilot", {"text": "  ", "use_llm": False})
check("empty text -> 400", status == 400, f"{status}")

# No LLM key: use_llm=True degrades gracefully to the rule engine.
status, body = api_post("/api/copilot", {
    "text": "get the address for 2ISW916B",
    "use_llm": True,
})
check("no-key LLM request still 200", status == 200, f"{status} {body}")
if status == 200:
    check("mode deterministic without key", body.get("mode") == "deterministic",
          body.get("mode"))

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
