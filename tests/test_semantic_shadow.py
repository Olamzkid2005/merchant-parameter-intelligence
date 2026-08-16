"""
test_semantic_shadow.py — Phase 1 Tier-2 semantic shadow layer (offline).

Covers merchant_intelligence/tasks/semantic.py + the feature-flag knob in
settings.py + the suggest_clarification() hook in engine.py. No DB, no
network, no LLM — the pure-Python HashingEncoder runs anywhere.

Run:  python tests/test_semantic_shadow.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merchant_intelligence import settings as engine_settings
from merchant_intelligence.tasks import analyze, suggest_clarification
from merchant_intelligence.tasks import semantic
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


def _set_env(**kw):
    saved = {}
    for k, v in kw.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# Hermetic: test the deterministic pure-Python hashing encoder regardless
# of whether an ONNX model artifact exists on this machine (the ONNX path is
# covered separately by the baseline measurement + export probe).
os.environ.setdefault("MERCHANT_TIER2_ENCODER", "hash")
# Hermetic: never read the machine's persisted engine settings (data/
# engine_settings.json may legitimately hold a saved mode from real use) —
# point the knob at a temp, empty file so every check sees the defaults.
os.environ.setdefault(
    "ENGINE_SETTINGS_FILE",
    str(Path(tempfile.mkdtemp(prefix="ss_knob_")) / "settings.json"))


# ── [1] settings knob ─────────────────────────────────────────────────────
print("\n[1] semantic_tier_mode knob")
check("defaults to 'off'", engine_settings.semantic_tier_mode() == "off",
      engine_settings.semantic_tier_mode())
check("all_settings exposes the UI contract",
      engine_settings.all_settings()["semantic_tier_mode"]["value"] == "off"
      and engine_settings.all_settings()["semantic_tier_mode"]["default"] == "off"
      and engine_settings.all_settings()["semantic_tier_mode"]["valid_range"]
      == ["off", "shadow", "enabled"],
      repr(engine_settings.all_settings()["semantic_tier_mode"]))
_saved = _set_env(SEMANTIC_TIER_MODE="shadow")
try:
    check("env var flips to shadow",
          engine_settings.semantic_tier_mode() == "shadow",
          engine_settings.semantic_tier_mode())
finally:
    _restore_env(_saved)
_saved = _set_env(SEMANTIC_TIER_MODE="bogus")
try:
    check("invalid mode falls back to off",
          engine_settings.semantic_tier_mode() == "off",
          engine_settings.semantic_tier_mode())
finally:
    _restore_env(_saved)

# ── [2] exemplars (Phase-A cold start) ────────────────────────────────────
print("\n[2] exemplar cold start")
_ex = semantic.load_exemplars()
check("every vocab intent has >=1 exemplar",
      all(_ex.get(i) for i in INTENT_KEYWORDS),
      f"missing: {[i for i in INTENT_KEYWORDS if not _ex.get(i)]}")
check("static_account exemplars present",
      any("static account" in p for p in _ex.get("static_account", [])),
      repr(_ex.get("static_account", [])[:3]))

# ── [3] query masking ─────────────────────────────────────────────────────
print("\n[3] mask_query")
_masked = semantic.mask_query(
    "get the static account for MX141692 and phone for MEDPLUS",
    {"names": ["MEDPLUS"], "identifiers": {"mx": ["MX141692"]}})
check("identifier value replaced by [MX]",
      "MX141692" not in _masked and "[MX]" in _masked, _masked)
_masked_low = semantic.mask_query(
    "who handles the money for medplus",
    {"names": ["MEDPLUS"]})
check("name masked case-insensitively",
      "medplus" not in _masked_low and "[MERCHANT]" in _masked_low, _masked_low)
_masked_id = semantic.mask_query("is 2103O338 registered")
check("unparsed identifier shapes swept to [ID]",
      "2103O338" not in _masked_id and "[ID]" in _masked_id, _masked_id)

# ── [4] resolve ───────────────────────────────────────────────────────────
print("\n[4] semantic.resolve")
_r = semantic.resolve("who handles the money for MEDPLUS",
                      {"names": ["MEDPLUS"]})
check("resolve returns a well-formed decision",
      _r is not None and _r.get("intent") in INTENT_KEYWORDS
      and isinstance(_r.get("confidence"), int)
      and 0 <= _r["confidence"] <= 100
      and _r.get("exemplar") and "would_act" in _r and "margin" in _r,
      repr(_r))
check("resolve is cached (identical result for identical input)",
      semantic.resolve("who handles the money for MEDPLUS",
                       {"names": ["MEDPLUS"]}) is _r)
check("calibration bounds",
      semantic._calibrate(0.2) == 0 and semantic._calibrate(0.85) == 100
      and semantic._calibrate(0.575) >= semantic._calibrate(0.3),
      f"{semantic._calibrate(0.2)}, {semantic._calibrate(0.85)}")
_saved = _set_env(SEMANTIC_TIER_MODE=None, MERCHANT_TIER2_SHADOW_FILE=None)
try:
    _orig = semantic.load_exemplars
    semantic.load_exemplars = lambda: {}
    try:
        check("resolve returns None with no exemplars",
              semantic.resolve("anything") is None)
    finally:
        semantic.load_exemplars = _orig
finally:
    _restore_env(_saved)

# ── [5] suggest_clarification hook (mode off / shadow / enabled) ──────────
print("\n[5] suggest_clarification tier-2 hook")
_tmp = tempfile.mkdtemp()
_shadow_file = str(Path(_tmp) / "tier2_shadow.jsonl")
_saved = _set_env(SEMANTIC_TIER_MODE=None, MERCHANT_TIER2_SHADOW_FILE=_shadow_file)
try:
    check("mode off: no shadow file written",
          not Path(_shadow_file).exists())
    c = suggest_clarification("account details for MEDPLUS")
    check("mode off: clarification card unchanged (no auto_pick)",
          c is not None and set(c) == {"question", "options"},
          repr(c and sorted(c)))
finally:
    _restore_env(_saved)

_saved = _set_env(SEMANTIC_TIER_MODE="shadow", MERCHANT_TIER2_SHADOW_FILE=_shadow_file)
try:
    c = suggest_clarification("account details for MEDPLUS")
    check("shadow: response still asks the user (no auto_pick)",
          c is not None and set(c) == {"question", "options"},
          repr(c and sorted(c)))
    entries = semantic.read_shadow()
    check("shadow: one decision logged",
          len(entries) == 1, f"{len(entries)}")
    if entries:
        e = entries[0]
        check("shadow entry carries tier2 fields",
              e.get("tier2_intent") in INTENT_KEYWORDS
              and "tier2_exemplar" in e and "tier2_confidence" in e
              and "tier2_would_act" in e and e.get("would_clarify") is True
              and e.get("encoder") == semantic._ENCODER_ID,
              repr({k: e.get(k) for k in
                    ("tier1_intent", "tier2_intent", "tier2_confidence")}))
    # A decisive request must never reach the tier (no card -> no hook).
    suggest_clarification("get the static account for MX141692")
    check("shadow: decisive request logs nothing",
          len(semantic.read_shadow()) == 1, f"{len(semantic.read_shadow())}")
    # The debug panel exposes the shadow decision (explainability, §8).
    a = analyze("account details for MEDPLUS")
    check("analyze exposes tier2",
          (a.get("tier2") or {}).get("intent") in INTENT_KEYWORDS,
          repr(a.get("tier2")))
finally:
    _restore_env(_saved)

_saved = _set_env(SEMANTIC_TIER_MODE="enabled", MERCHANT_TIER2_SHADOW_FILE=_shadow_file)
try:
    c = suggest_clarification("account details for MEDPLUS")
    check("enabled: confident tier2 auto-picks instead of asking",
          c is not None and c.get("auto_pick") in INTENT_KEYWORDS
          and c.get("tier2", {}).get("intent") == c.get("auto_pick"),
          repr(c and {k: c.get(k) for k in ("auto_pick", "tier2")}))
    check("enabled: decision still shadow-logged",
          len(semantic.read_shadow()) >= 2, f"{len(semantic.read_shadow())}")
finally:
    _restore_env(_saved)

# ── Versioned embedding artifacts (design doc §9) ────────────────────────
# Precomputed exemplar vectors persist to data/exemplar_embeddings_<enc>_<fp>.
# npy so a restart cold-starts from disk instead of re-encoding ~190 phrases.
print("\n[10] versioned embedding artifact (round-trip + staleness)")
from merchant_intelligence import config as _config
_old_dir = _config.DATA_DIR
_tmp_dir = Path(tempfile.mkdtemp(prefix="emb_art_"))
_config.DATA_DIR = _tmp_dir
try:
    _ex = {"tid": ["get the device ids", "terminal ids please"],
           "bank": ["which bank"]}
    _enc = semantic._make_encoder()
    _v1 = semantic._get_exemplar_vecs(_ex, _enc)
    _fp = semantic._fingerprint(_ex)
    _art = semantic._exemplar_vecs_path(_enc.id, _fp)
    check("artifact written, versioned by encoder + fingerprint",
          _art.exists() and _art.with_suffix(".json").exists(),
          _art.name)
    check("filename embeds encoder id", _enc.id in _art.name)
    # Cold reload: drop the in-memory cache, must read from disk.
    semantic._exemplar_vecs.clear()
    _v2 = semantic._get_exemplar_vecs(_ex, _enc)
    check("cold reload reads the artifact", _v2 is not None
          and len(_v2.get("tid", [])) == 2)
    _c1 = semantic._cosine(_enc.encode(["get the device ids"])[0],
                           _v2["tid"][0][1])
    check("reloaded vectors functionally match (cosine ~1.0)",
          round(_c1, 3) == 1.0, repr(_c1))
    # Staleness: an exemplar edit produces a NEW fingerprint + artifact.
    _ex2 = dict(_ex)
    _ex2["tid"] = _ex["tid"] + ["brand new phrase here"]
    semantic._exemplar_vecs.clear()
    _v3 = semantic._get_exemplar_vecs(_ex2, _enc)
    _art2 = semantic._exemplar_vecs_path(_enc.id, semantic._fingerprint(_ex2))
    check("exemplar edit rebuilds a distinct artifact",
          _art2.exists() and _art2 != _art and len(_v3["tid"]) == 3,
          f"{_art.name} vs {_art2.name}")
    # Corruption: a mangled sidecar must not crash the load path.
    _art.with_suffix(".json").write_text("not json", encoding="utf-8")
    semantic._exemplar_vecs.clear()
    check("corrupt sidecar -> None, no crash",
          semantic._load_exemplar_vecs((_fp, _enc.id), _ex, _enc) is None)
finally:
    _config.DATA_DIR = _old_dir

print(f"\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
