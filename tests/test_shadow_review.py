"""
test_shadow_review.py — hermetic tests for the Tier-2 shadow-review tooling
(design doc §7 Phase-1 spot-check: the high-confidence auto-run band that
clarification labels never cover).

Covers the semantic.py review section: stable entry ids, band-filtered
review(), label write/re-label semantics, and per-intent precision stats.
Every test runs against TEMP shadow + review files (env seams), so the real
data/ logs are never touched and the suite needs no live server.

Run:  python tests/test_shadow_review.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence.tasks import semantic  # noqa: E402

# ── Test harness ─────────────────────────────────────────────────────────
_passed = 0
_failed = 0


def check(name, cond, info=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}  {info}")


def _make_entries():
    """Two would-act decisions + one would-not, with distinct texts."""
    return [
        {
            "ts": 1700000000.0,
            "text": "get me the old and new account for SPAR",
            "mode": "shadow",
            "tier1_intent": "change_details",
            "tier2_intent": "change_details",
            "tier2_exemplar": "old account",
            "tier2_confidence": 91,
            "tier2_margin": 40,
            "tier2_would_act": True,
            "encoder": "onnx-all-MiniLM-L6-v2",
        },
        {
            "ts": 1700000001.0,
            "text": "who do we pay for LAGOON WATERS",
            "mode": "shadow",
            "tier1_intent": "static_account",
            "tier2_intent": "static_account",
            "tier2_exemplar": "where payment is received",
            "tier2_confidence": 78,
            "tier2_margin": 22,
            "tier2_would_act": True,
            "encoder": "onnx-all-MiniLM-L6-v2",
        },
        {
            "ts": 1700000002.0,
            "text": "account details for MEDPLUS",
            "mode": "shadow",
            "tier1_intent": "change_details",
            "tier2_intent": "profile",
            "tier2_exemplar": "details",
            "tier2_confidence": 42,
            "tier2_margin": 3,
            "tier2_would_act": False,
            "encoder": "onnx-all-MiniLM-L6-v2",
        },
    ]


def _setup():
    """Temp shadow log + review label file via the env seams."""
    tmp = tempfile.mkdtemp(prefix="sr_test_")
    shadow = Path(tmp) / "shadow.jsonl"
    review = Path(tmp) / "review.jsonl"
    shadow.write_text(
        "\n".join(json.dumps(e) for e in _make_entries()) + "\n",
        encoding="utf-8")
    os.environ["MERCHANT_TIER2_SHADOW_FILE"] = str(shadow)
    os.environ["MERCHANT_TIER2_REVIEW_FILE"] = str(review)
    # No test may leak env into other runs.
    os.environ["SEMANTIC_TIER_MODE"] = "off"
    return shadow, review


def _teardown():
    for k in ("MERCHANT_TIER2_SHADOW_FILE", "MERCHANT_TIER2_REVIEW_FILE"):
        os.environ.pop(k, None)


# ── Tests ────────────────────────────────────────────────────────────────
print("\n[1] entry ids + band filtering")
_shadow, _review = _setup()
try:
    _entries = semantic.read_shadow()
    check("read_shadow reads 3 entries", len(_entries) == 3, str(len(_entries)))
    _ids = {semantic.entry_id(e) for e in _entries}
    check("entry ids are stable + unique",
          len(_ids) == 3 and all(len(i) == 12 for i in _ids),
          repr(_ids))
    # Same text+ts -> same id (append-only log re-reads stay labelable).
    _dup = dict(_entries[0])
    check("id stable across reads",
          semantic.entry_id(_dup) == semantic.entry_id(_entries[0]))
    _wa = semantic.review(band="would_act")
    check("would_act band has 2 entries", _wa["count"] == 2,
          f"count={_wa['count']}")
    _wn = semantic.review(band="would_not")
    check("would_not band has 1 entry", _wn["count"] == 1,
          f"count={_wn['count']}")
    _all = semantic.review(band="all")
    check("all band has 3 entries", _all["count"] == 3,
          f"count={_all['count']}")
    check("review attaches label=None initially",
          all(e.get("label") is None for e in _all["entries"]))
finally:
    _teardown()

print("\n[2] labeling + re-label (latest wins)")
_shadow, _review = _setup()
try:
    _entries = semantic.read_shadow()
    _e1 = semantic.entry_id(_entries[0])   # change_details, would_act
    _r = semantic.label_entry(_e1, True)
    check("label_entry returns ok", _r.get("ok") is True, repr(_r))
    _labels = semantic.read_review()
    check("label persisted", _e1 in _labels and _labels[_e1]["correct"] is True,
          repr(sorted(_labels)))
    # Re-label flips the verdict and does not duplicate the row.
    semantic.label_entry(_e1, False, intent="profile", note="should be profile")
    _labels = semantic.read_review()
    check("re-label latest wins", _labels[_e1]["correct"] is False
          and _labels[_e1]["intent"] == "profile"
          and _labels[_e1]["note"] == "should be profile",
          repr(_labels[_e1]))
    check("re-label keeps single row", len(_labels) == 1, str(len(_labels)))
    # Review view reflects the label.
    _wa = semantic.review(band="would_act")
    _hit = next(e for e in _wa["entries"] if e["entry_id"] == _e1)
    check("review entry carries the label",
          _hit["label"] and _hit["label"]["correct"] is False,
          repr(_hit.get("label")))
finally:
    _teardown()

print("\n[3] per-intent precision on the would-act band")
_shadow, _review = _setup()
try:
    _entries = semantic.read_shadow()
    _e1 = semantic.entry_id(_entries[0])   # change_details
    _e2 = semantic.entry_id(_entries[1])   # static_account
    semantic.label_entry(_e1, True)
    semantic.label_entry(_e2, True)
    _s = semantic.review_stats()
    check("reviewed == 2", _s["reviewed"] == 2, repr(_s))
    check("band_total == 2 (would_act only)",
          _s["band_total"] == 2, repr(_s))
    check("precision == 1.0", _s["precision"] == 1.0, repr(_s))
    check("per-intent counts", _s["per_intent"]["change_details"]["reviewed"] == 1
          and _s["per_intent"]["static_account"]["reviewed"] == 1,
          repr(_s["per_intent"]))
    # Mark one wrong WITH the actual intent -> precision drops, miss recorded.
    semantic.label_entry(_e2, False, intent="payable")
    _s = semantic.review_stats()
    check("precision drops to 0.5", _s["precision"] == 0.5, repr(_s))
    check("miss recorded as missed_as",
          "payable" in _s["per_intent"]["static_account"]["missed_as"],
          repr(_s["per_intent"]))
    # A wrong label WITHOUT an intent still counts in precision.
    _e3 = semantic.entry_id(_entries[2])
    semantic.label_entry(_e3, False)  # would_not entry — not in band stats
    _s = semantic.review_stats()
    check("would_not label ignored by band stats",
          _s["reviewed"] == 2, repr(_s))
finally:
    _teardown()

print("\n[4] empty / corrupt handling")
_shadow, _review = _setup()
try:
    _shadow.write_text("this is not json\n{\"partial\": \n", encoding="utf-8")
    check("corrupt shadow lines skipped", semantic.read_shadow() == [],
          repr(semantic.read_shadow()))
    _review.write_text("garbage\n", encoding="utf-8")
    check("corrupt review lines skipped", semantic.read_review() == {},
          repr(semantic.read_review()))
    _s = semantic.review_stats()
    check("empty stats shape", _s["reviewed"] == 0 and _s["band_total"] == 0,
          repr(_s))
finally:
    _teardown()

print("\n[5] append_exemplar (Phase B, enrichment module)")
from merchant_intelligence.tasks import enrichment  # noqa: E402
_tmp2 = tempfile.mkdtemp(prefix="sr_ex_")
try:
    from merchant_intelligence import config
    _ex = Path(_tmp2) / "exemplars.json"
    _ex.write_text(json.dumps({"intents": {"static_account": ["static account"]}}),
                   encoding="utf-8")
    _old = config.DATA_DIR
    config.DATA_DIR = Path(_tmp2)
    try:
        check("append adds the phrase",
              enrichment.append_exemplar("static_account", "funds landing account") is True)
        _data = json.loads(_ex.read_text(encoding="utf-8"))
        check("phrase persisted",
              "funds landing account" in _data["intents"]["static_account"],
              repr(_data["intents"]["static_account"]))
        check("duplicate is a no-op",
              enrichment.append_exemplar("static_account", "funds landing account") is False)
        _data2 = json.loads(_ex.read_text(encoding="utf-8"))
        check("duplicate not written twice",
              _data2["intents"]["static_account"].count("funds landing account") == 1)
        # Missing-file path: drop the file, then append must fail cleanly.
        _ex.unlink()
        check("missing file returns False",
              enrichment.append_exemplar("static_account", "x y z") is False)
        check("missing file does not recreate it",
              not _ex.exists())
    finally:
        config.DATA_DIR = _old
finally:
    pass

print("\n[6] fit_tier2: per-intent gates from review labels (Phase 3)")
from merchant_intelligence import calibration  # noqa: E402
_old_min = calibration.TIER2_PER_INTENT_MIN
calibration.TIER2_PER_INTENT_MIN = 2


def _t2_entry(intent, conf, margin, would_act, text, ts):
    return {
        "ts": ts, "text": text, "mode": "shadow", "tier1_intent": None,
        "tier2_intent": intent, "tier2_exemplar": "x",
        "tier2_confidence": conf, "tier2_margin": margin,
        "tier2_would_act": would_act, "encoder": "onnx-test",
    }


def _t2_write(tmp, entries, labels):
    """entries: list of shadow dicts; labels: [(index, correct, intent)]"""
    shadow = Path(tmp) / "shadow.jsonl"
    review = Path(tmp) / "review.jsonl"
    shadow.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                      encoding="utf-8")
    lines = []
    for idx, correct, intent in labels:
        eid = semantic.entry_id(entries[idx])
        lines.append(json.dumps({
            "entry_id": eid, "ts": time.time(), "correct": bool(correct),
            "intent": intent, "note": ""}))
    review.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["MERCHANT_TIER2_SHADOW_FILE"] = str(shadow)
    os.environ["MERCHANT_TIER2_REVIEW_FILE"] = str(review)
    return shadow, review


try:
    _tmp = tempfile.mkdtemp(prefix="sr_fit_")
    # Solid would-act band (conf 70-79, all correct) -> threshold drops to 60.
    _ents = [_t2_entry("static_account", 72, 20, True, "funds for LAGOON WATERS", 1.0),
             _t2_entry("static_account", 74, 22, True, "who gets paid for BOKKU", 2.0),
             _t2_entry("static_account", 76, 24, True, "payable account of GAJI TAIWO", 3.0)]
    _t2_write(_tmp, _ents, [(0, True, ""), (1, True, ""), (2, True, "")])
    _f = calibration.fit_tier2()
    _g = _f["per_intent"]["static_account"]
    check("solid 60-79 band drops threshold to 60",
          _g["threshold"] == 60, repr(_g))
    check("precision 1.0 on all-correct", _g["precision"] == 1.0, repr(_g))
    check("fit active", _f["active"] is True, repr(_f))

    # Poor acceptance in the band raises the gate to the band's top edge.
    _tmp2 = tempfile.mkdtemp(prefix="sr_fit2_")
    _ents2 = [_t2_entry("coverage", 70, 20, True, "email for SPAR", 1.0),
              _t2_entry("coverage", 73, 22, True, "phone for ADDIDE", 2.0),
              _t2_entry("coverage", 76, 24, True, "bank for RUBELS", 3.0)]
    _t2_write(_tmp2, _ents2, [(0, False, "email"), (1, False, "phone"),
                              (2, True, "")])
    _f2 = calibration.fit_tier2()
    _g2 = _f2["per_intent"]["coverage"]
    check("poor 60-79 band raises threshold to 80 (band top edge)",
          _g2["threshold"] == 80, repr(_g2))
    check("precision 0.333", round(_g2["precision"], 3) == 0.333, repr(_g2))

    # Correct would-not picks below the gate lower it (ONNX under-mapped band).
    _tmp3 = tempfile.mkdtemp(prefix="sr_fit3_")
    _ents3 = [_t2_entry("profile", 72, 20, True, "details of MEDPLUS", 1.0),
              _t2_entry("profile", 74, 22, True, "profile for FILMHOUSE", 2.0),
              _t2_entry("profile", 76, 24, True, "info on ARTEE INDUSTRIES", 3.0),
              _t2_entry("profile", 45, 5, False, "who is ADDIDE AGUDA", 4.0),
              _t2_entry("profile", 48, 6, False, "tell me about WSV VENTURES", 5.0),
              _t2_entry("profile", 52, 8, False, "about MONEYTRUST", 6.0)]
    _t2_write(_tmp3, _ents3, [(0, True, ""), (1, True, ""), (2, True, ""),
                              (3, True, ""), (4, True, ""), (5, True, "")])
    _f3 = calibration.fit_tier2()
    _g3 = _f3["per_intent"]["profile"]
    check("would-not-correct lowers threshold to 40",
          _g3["threshold"] == 40, repr(_g3))
    check("would_not_correct counted", _g3["would_not_correct"] == 3, repr(_g3))

    # Insufficient samples -> no gate, progress shown.
    _tmp4 = tempfile.mkdtemp(prefix="sr_fit4_")
    _ents4 = [_t2_entry("tid", 80, 30, True, "device ids for LAGOON WATERS", 1.0)]
    _t2_write(_tmp4, _ents4, [(0, True, "")])
    _f4 = calibration.fit_tier2()
    _g4 = _f4["per_intent"]["tid"]
    check("under-min intent has no gate", _g4["threshold"] is None, repr(_g4))
    check("progress shows samples/needed",
          _g4["samples"] == 1 and _g4["needed"] == 2, repr(_g4))

    # Global margin fit from all labeled would-act decisions.
    _tmp5 = tempfile.mkdtemp(prefix="sr_fit5_")
    _ents5 = [_t2_entry("bank", 70, 12, True, "bank for MEDPLUS", 1.0),
              _t2_entry("bank", 73, 13, True, "bank of SPAR", 2.0),
              _t2_entry("bank", 76, 14, True, "banker for FILMHOUSE", 3.0)]
    _t2_write(_tmp5, _ents5, [(0, True, ""), (1, True, ""), (2, True, "")])
    _f5 = calibration.fit_tier2()
    check("global margin fit to 10", _f5["margin"] == 10, repr(_f5))

    # semantic._gate_for consults the CURRENT fitted state. Re-point the
    # seams at the static_account scenario (threshold 60, margin stays 15:
    # its 20-29 margins hit the band floor 15 first), then the bank scenario
    # (margin 10) to show the global margin applies to non-fitted intents.
    _tmp7 = tempfile.mkdtemp(prefix="sr_gate_")
    _t2_write(_tmp7, _ents, [(0, True, ""), (1, True, ""), (2, True, "")])
    _gate = semantic._gate_for("static_account")
    check("gate_for uses fitted threshold", _gate[0] == 60, repr(_gate))
    _gate3 = semantic._gate_for("no_such_intent")
    check("gate_for falls back to default threshold + fitted margin",
          _gate3 == (semantic.SEMANTIC_THRESHOLD, 15), repr(_gate3))
    _tmp8 = tempfile.mkdtemp(prefix="sr_gate2_")
    _t2_write(_tmp8, _ents5, [(0, True, ""), (1, True, ""), (2, True, "")])
    _gate2 = semantic._gate_for("bank")
    check("gate_for applies fitted global margin", _gate2[1] == 10, repr(_gate2))
finally:
    calibration.TIER2_PER_INTENT_MIN = _old_min
    for k in ("MERCHANT_TIER2_SHADOW_FILE", "MERCHANT_TIER2_REVIEW_FILE"):
        os.environ.pop(k, None)

print("\n============================================================")
print(f"  RESULT: {_passed} passed, {_failed} failed")
print("============================================================")
sys.exit(1 if _failed else 0)
