# Design Doc: Hybrid Semantic Intent Layer (Embeddings + Dictionary-Enriched Exemplars)

**Status:** Draft v4 — corrected against source code
**Owner:** David
**Scope:** `merchant_intelligence/tasks/` (intent parser)
**Related:** `docs/technical-review-2026-08.md` (§4 Agentic Copilot), `intents.json`, `calibration.py`, `feedback.py`

**Changelog v3 → v4 (code-grounded corrections — all verified against the actual repo, not inferred):**
- §2 corrected: an offline semantic fallback tier already exists (`_phrase_similarity`, capped at confidence 48). Baseline must measure against current behavior *including* this tier.
- §4 schema corrected: patterns are `{pattern, weight}` **dicts**, not tuples, and each intent also has a separate `keywords` list (soft fuzzy phrases) + a `fuzzy` toggle. The v3 "exact tuple shape" claim was wrong.
- §4 **[4h] parity-test collision resolved** — this was the biggest unmet risk in v3. `build_exemplars.py` must regenerate `vocab.py`'s `_DEFAULT_INTENT_PATTERNS` in lockstep with any `intents.json` merge, or the existing test suite breaks on the first build.
- §4 "never independently trigger" softened to "below every confidence-gated threshold" — `detect_task`'s `is_task` branches key on intent *identity*, not confidence, in several places, so this is a real residual risk, not a solved one.
- §5 corrected: `golden.py` is a merchant-*matching* benchmark (query → confirmed email/name), zero intent labels — it cannot source intent exemplars. The Phase 0 held-out set is the only intent-level ground truth that exists, and becomes the ongoing one.
- §5 Phase B now explicitly extends `feedback.py`'s existing `mine_patterns()`/`apply_pattern()` loop instead of describing a parallel mechanism.
- §7 shadow-mode blind spot acknowledged: clarification-outcome labels only cover the ambiguous band; confident Tier 2 auto-runs never reach the picker, so Phase 2's go/no-go must say so explicitly.
- Integration point pinned to `suggest_clarification()` (`engine.py:918`), called from both `api.py:943` and the shared `analyze()` path — one hook, not two. Tier 2 hits tagged `~embedding:...` to reuse the existing `explicit_top` tilde-prefix handling for free.
- Feature flag, new-dependency, and data-versioning scope corrections (§9, §11).

---

## 1. Summary

Add a local, offline, "close-to-LLM" semantic layer on top of the existing deterministic intent parser, without introducing an LLM, a network call, or any change to the parser's current authority. Two tiers:

1. **Tier 1 (enriched, build-time):** the existing regex patterns, now auto-expanded with human-approved WordNet synonyms baked directly into `intents.json` (and `vocab.py`'s defaults — see §4) at build time.
2. **Tier 2 (new, runtime fallback):** a local embedding model matches the query against per-intent exemplar phrases when Tier 1 — including its *existing* semantic fallback (§2) — is inconclusive.

**What this layer does not do:** it buys paraphrase tolerance for known intents, not multi-step reasoning. A compound request like "Lagos merchants sharing an identifier with TID 12345, excluding SPAR" stays out of scope — that's the domain of a future agentic layer (`docs/technical-review-2026-08.md` §4), not this one.

---

## 2. Current state (baseline) — corrected

- `intents.py`: confidence = `min(100, score * 12)` (`intents.py:68`, documented in `intents.json`'s own `_help`).
- **An offline semantic fallback tier already exists and must be part of the baseline.** `_phrase_similarity()` (`intents.py:129`) is an order-tolerant, plural-tolerant keyword-coverage scorer with within-one-edit Damerau-fuzzy matching. It fires when a request misses every regex pattern but overlaps an intent's `keywords`. Hits are capped at `_SEMANTIC_MAX_SCORE = 4.0` → **confidence 48**, and tagged `~semantic:0.88` / `~fuzzy:0.92` in `matched` so the engine debug panel shows why. The cap is deliberate: a paraphrase-only match can reach clarification but can never auto-run or auto-clarify past `CLARIFY_TOP_MAX = 60` on its own.
- `suggest_clarification()` (`engine.py:918`): race window `CLARIFY_GAP = 4.0`, ask ceiling `CLARIFY_TOP_MAX = 60` (or the fitted `calibration.py` threshold when active), vague-intent handling, and remembered-choice auto-pick.
- **What this design actually changes, stated precisely:** today, semantic-only matches (the `~semantic`/`~fuzzy` tier) are capped at confidence 48, so they can never *cross* `CLARIFY_TOP_MAX = 60` — no clarification-skip, no high-confidence auto-run. (A narrow set of `detect_task` branches gated at `top_conf >= 40` — the question-word and key-merchant branches, `engine.py:243-267` — can in principle route a semantic-only match into a task today; the airtight claim is about the 60-point line.) Tier 2's calibrated score (§6) is explicitly allowed to exceed 60. **This is the first path where a paraphrase-only match can auto-run a pipeline with no human-in-the-loop confirmation.** That is a real behavioral change from today's system, and it is why the margin gate (§6) and the Phase 0 baseline (§7) are load-bearing, not optional hardening.

---

## 3. Proposed architecture

```
 query
   │
   ▼
 Tier 1 — Regex patterns (enriched, §4) + existing semantic fallback (§2,
          capped at 48)
   │  clears gate? ──yes──► done (still the single deterministic authority)
   │  no / ambiguous (including a capped ~semantic hit)
   ▼
 Tier 2 — Embedding semantic match (NEW), calibrated to the 0-100 scale (§6)
   │  clears threshold + margin over 2nd-best? ──yes──► done, tagged
   │                                                     ~embedding:... (§8)
   │  no
   ▼
 suggest_clarification() — existing, single hook (§10)
```

---

## 4. Tier 1 enrichment pipeline (build-time) — corrected

**Actual schema** (`intents.json`, `vocab.py`): each intent is `{"patterns": [{"pattern": <regex>, "weight": <int>}, ...], "keywords": [<word>, ...], "fuzzy": <bool>}`. `patterns` drives regex scoring; `keywords` doubles as the soft input to the existing `_phrase_similarity` fallback (§2). This is not a `(regex, weight)` tuple list — v3's "exact tuple shape" claim was wrong.

1. Extract literal phrases from existing `patterns` (cold start — §5).
2. Expand each via WordNet (`nltk.corpus.wordnet`, downloaded once, fully local afterward).
3. **Curation gate — human review, not auto-accept**, on the Rule Engine page (§10), same pattern as Alias Review. WordNet proposes domain-inappropriate synonyms ("beneficiary" → "heir", "donee") that must be filtered before they touch anything live.
4. Approved synonyms feed **two outputs**:
   - Merged into `intents.json` as new `patterns` entries, fixed low weight (**2**, rationale below). **Explicit decision recorded here, not deferred:** auto-synonyms join `patterns` only, not `keywords` — joining `keywords` would also feed the existing `_phrase_similarity` fallback (§2), doubling the enrichment surface and making the two tiers' interaction harder to reason about. This can be revisited once Tier 2 has real precision data.
   - Written to `data/exemplars.json` + a versioned `data/exemplar_embeddings_<model>_<version>.npy` for Tier 2 (§11).
5. **`build_exemplars.py` regenerates `vocab.py`'s `_DEFAULT_INTENT_PATTERNS` in the same run, from the same approved output, before either file is written.** This is not optional: `tests/test_tasks.py`'s `[4h]` parity check asserts `INTENT_PATTERNS == _DEFAULT_INTENT_PATTERNS` (and the same for keywords) against the shipped config — a config-only merge fails that test on the first run, exactly as `intents.json`'s own onboarding notes warn for any new intent. Under the patterns-only decision (§4.4) `_DEFAULT_INTENT_KEYWORDS` never changes, so it only needs regeneration if a future decision admits synonyms there too. Provenance/revertability for the auto entries lives in a separate `data/auto_pattern_manifest.json` (regex → intent → weight → generated_from → approved_by/date) — auditable at the application layer even though `intents.json`/`vocab.py` themselves carry no provenance tag inline.
6. Re-run on demand when `intents.json` changes meaningfully.

   *Status: DONE — `merchant_intelligence/tasks/enrichment.py` (propose / status / apply with the §4.5 vocab.py lockstep regeneration, manifest, exemplar append) + `scripts/enrich_intents.py` CLI + `GET /api/synonyms` / `POST /api/synonyms/propose|status|apply` + `GET /api/synonyms/manifest` + a WordNet proposal panel on the Rule Engine page. WordNet corpus installed locally; first proposal batch in `data/exemplar_candidates.json`. `feedback.apply_pattern()` now also regenerates `vocab.py` defaults in lockstep (fixes the pre-existing drift that silently broke the `[4h]` parity test). Hermetic suite `tests/test_enrichment.py` (38 checks, fake synsets + temp config/vocab).*

**Why weight 2:** confidence `min(100, 2*12) = 24` — below every confidence-*gated* threshold in the system (`CLARIFY_TOP_MAX = 60`, and the `>= 40`-style gates used elsewhere), so a lone auto-synonym can't push an intent through a numeric gate on its own; it only tips the balance summed with another matching pattern. **Correction from v3: this is "below every confidence-gated threshold," not "never triggers."** `detect_task`'s `is_task` branches (`engine.py`, e.g. the `related`/`formerly`/`verify` branches) key on `intents[0]` — the top-scored intent by *identity*, not a confidence check — so a weight-2 auto-pattern that flips which intent scores highest on an identifier- or name-bearing request can still change `is_task` routing, with no gate to stop it. This is the same risk class the system already accepts today for weight-3 keywords like `"info"`; it's named here explicitly so it isn't quietly overclaimed as "impossible."

---

## 5. Exemplar sourcing — two-phase, corrected

**Phase A (cold start, day one):** literal `patterns` phrases + WordNet expansion (§4). Gets Tier 2 usable immediately.

**Phase B (warm, once usage exists) — extends `feedback.py`, doesn't duplicate it.** `feedback.py` already mines real corrected/rephrased user queries into regex-pattern suggestions (`mine_patterns()`, `MIN_PATTERN_SAMPLES = 3`, weight range 4–7, curated on the Rule Engine page, applied via `apply_pattern()`, which hot-reloads `intents.json`). Phase B reuses that **same mined n-gram data** as the exemplar source for Tier 2, rather than building a second, competing mining loop that writes to the same files independently. Concretely: whenever a mined n-gram clears `MIN_PATTERN_SAMPLES` and is approved, it becomes *both* a candidate regex pattern (existing behavior, unchanged) *and* a candidate Tier 2 exemplar (new) — one curated approval, two consumers.

**`golden.py` is not an exemplar source — correction from v3.** It's a merchant-*matching* benchmark (`query → confirmed email/name`), with zero intent labels; using it for Phase B was wrong. **The Phase 0 held-out set (§7) is the only intent-level ground truth that exists in this codebase, and it becomes the ongoing intent golden set** (`merchant_intelligence/intent_golden.py`) — extended over time from approved Phase B exemplars, kept separate from `golden.py`'s merchant-matching benchmark.

**Trigger for A→B transition:** an intent moves to Phase B once `feedback.py` has logged **≥25 successful clarification resolutions** for it — a starting heuristic, tunable, applied per-intent independently.

---

## 6. Confidence-scale bridge (critical integration detail)

Cosine similarity lives in a tight ~0.4–0.9 band; regex confidence is `min(100, score * 12)`. Fit a calibration function (piecewise or logistic) mapping cosine similarity → the same 0–100 scale, fit from Phase 1 shadow data, so Tier 2 plugs into the *same* `CLARIFY_GAP`/`CLARIFY_TOP_MAX` gate logic Tier 1 already uses. As noted in §2, Tier 2's calibrated score is explicitly allowed to cross 60 — the existing `~semantic` fallback's cap is a design choice this layer is deliberately changing, not an oversight to preserve.

---

## 7. Rollout plan (baseline-gated, blind spot acknowledged)

1. **Phase 0 — Measure against the real current system, not "regex only."** Assemble a held-out set of novel phrasings. Run it against the **full current pipeline including the existing `~semantic` fallback** (§2) — not a stripped-down regex-only path — or the investment case is inflated. Document the failure/misroute rate. This is the Phase 2 go/no-go gate and becomes the ongoing intent golden set (§5). **ONNX export of the chosen embedding model is also a Phase 0 deliverable** (§9).
   *Status: DONE — `merchant_intelligence/intent_golden.py` (140 phrasings, 27 intents — extended with key-merchant ops phrasings) + `scripts/phase0_baseline.py`; baseline: 6% routed, 88% miss, 6% misroute, 0% clarify (snapshot `data/phase0_baseline.json`). Novelty contract + routing regression snapshot enforced by `tests/test_intent_golden.py` (9 checks; snapshot in `merchant_intelligence/intent_routing_snapshot.json`, refresh deliberately with `REBUILD_ROUTING_SNAPSHOT=1`).*
2. **Phase 1 — Shadow mode.** `semantic.py` behind a feature flag (§9), off by default. Shadow-log Tier 2 decisions with the §6 calibration fit. **Ground truth from clarification outcomes has a blind spot, stated plainly:** it only covers the ambiguous band (`conf < CLARIFY_TOP_MAX` or a `CLARIFY_GAP` race) — a Tier 2 match confident enough to *auto-run* (calibrated conf ≥ 60) never reaches the clarification picker, so it never gets a free label there. Phase 1 must therefore also include a small manual spot-check sample specifically from the high-confidence auto-run band before Phase 2 turns it on — the clarification-derived labels alone are not sufficient evidence for that band.
   *Status: DONE — `merchant_intelligence/tasks/semantic.py` behind the `semantic_tier_mode` knob (off default), hooked into `suggest_clarification()`; shadow decisions append to `data/tier2_shadow.jsonl` and the `analyze()` debug panel exposes them. ONNX production encoder SHIPPED: `scripts/export_embedding_model.py` (downloads the all-MiniLM-L6-v2 artifact to `data/models/`), `ONNXEncoder` loads it via onnxruntime + fast tokenizers with mean pooling, falls back to the zero-dep `HashingEncoder` when the artifact/deps are missing; encoder selectable via `MERCHANT_TIER2_ENCODER` (hash|onnx|auto). Curated intent-pure exemplars built by `scripts/build_exemplars.py` → `data/exemplars.json` (191 phrases / 27 intents). Golden-set preview (`scripts/phase0_baseline.py --tier2`, 140 phrasings): hashing encoder 62% top-1 / 41 correct would-acts; ONNX encoder 68% top-1 / 8 correct would-acts — ONNX's cosine band (median ~0.48, wrong picks up to 0.75) is under-mapped by the Phase-1 calibration, so per-intent threshold/margin re-fitting stays Phase 3 work on real shadow data.*
   *Status (spot-check tooling): DONE — the §7 auto-run-band manual spot-check is tooled: every shadow entry gets a stable `entry_id` (`semantic.entry_id`, ts+text md5), the reviewer labels entries correct/wrong (optionally naming the actual intent on a miss) via `label_entry()`, and `review()`/`review_stats()` band-filter the log (`all`/`would_act`/`would_not`) and aggregate per-intent precision on the would-act band — the Phase 2 go/no-go evidence. Surface: `GET/POST /api/shadow/review` (POST validates the entry_id against the log) + a review panel on the Rule Engine page; labels live in `data/tier2_shadow_review.jsonl` (gitignored). Hermetic coverage in `tests/test_shadow_review.py` (28 checks). Accumulation visibility: `semantic.shadow_health()` returns a band-independent summary (total / today / would-act / would-not / reviewed / newest-ts) attached to `GET /api/shadow/review` as `health`, rendered as a live chip on the Rule Engine Engine-tuning card (60s auto-poll) — watch the log grow without opening the panel.
3. **Phase 2 — Enable**, gated on both the Phase 0 baseline *and* the Phase 1 auto-run-band spot-check, not just the ambiguous-band labels. 4. **Phase 3 — Fold into calibration.** `calibration.py` absorbs Tier 2's threshold + margin; per-intent thresholds once accept/override data exists per intent (`MIN_SAMPLES = 20`, matching the existing banded acceptance scan). Retire the feature flag.
    *Status: DONE — `calibration.fit_tier2()` reads the §7 shadow-review labels (accept/override evidence on the auto-run band) and fits per-intent THRESHOLDS (banded acceptance scan, same solid/poor logic as the ask fit; ≥ `TIER2_PER_INTENT_MIN = 20` labeled would-act decisions per intent), a single GLOBAL margin fit (margin is intent competition, not identity), and a conservative LOWERING signal from correct would-not picks (≥3 in a band below the gate drop the threshold to that band's floor, floor `TIER2_LOWER_FLOOR = 40` — the ONNX under-mapped band fix). `semantic.resolve()` consults the fitted gates per request (`_gate_for()`), with the review-file state in the resolve lru-cache key so a label moves the gate immediately; falls back to the Phase-1 constants when an intent has no gate. Exposed via `/api/calibration` → `tier2` and `/api/shadow/review` → `tier2_fit`; the Rule Engine Shadow Review panel renders the per-intent gates + `samples/needed` progress. Hermetic coverage in `tests/test_shadow_review.py` (fit + gate wiring, 41 checks).*

---

## 8. Explainability

Every Tier 2 hit is tagged `~embedding:<score>` in `matched` (not a bare `matched_tier` field) — the `~` prefix is not cosmetic: `suggest_clarification()`'s existing `explicit_top` check already treats any `~`-prefixed match as non-explicit (`engine.py`, the `startswith("~")` check), exactly the same treatment the existing `~semantic`/`~fuzzy` tags get. This is inherited behavior, not new logic. The result also carries `matched_exemplar: "who do we pay"` — the specific phrase that won — for the same UI/badge and calibration-mining purpose as before.

---

## 9. Model choice, serving, and integration surface

| Model | Size | License | Role |
|---|---|---|---|
| **all-MiniLM-L6-v2** | ~80MB | Apache 2.0 | **Phase 0 default** — trivial ONNX path |
| bge-small-en-v1.5 | ~130MB | MIT | Second Phase 0 benchmark point |
| EmbeddingGemma-300M (Google) | ~200MB quantized | Gemma Terms of Use (open weights, commercial OK, Google-specific restrictions — *not* Apache 2.0) | Upgrade path only, evaluated after Phase 0; non-standard tokenizer/Matryoshka dims make ONNX harder |

The encoder is a config value; `semantic.py` doesn't hardcode which one.

**Feature flag — implemented as a tri-state mode knob (deviation from v4's bool).** `settings.py`'s `_SPEC` supports float knobs; the semantic tier ships as a **mode knob** `semantic_tier_mode` (`"off" | "shadow" | "enabled"`, default `"off"`) in a parallel `_MODE_SPEC` with the same env-var > file > default precedence. A tri-state maps 1:1 onto the rollout phases (off → shadow logging → enabled acting) and avoids a second `semantic_shadow` bool knob; the Rule Engine page only renders `decisive_match_threshold`, so the extra knob is backend-only and UI-safe.

**New dependencies — extend the existing preflight, don't bypass it.** `nltk` (+ one-time corpus download), `onnxruntime`, and the model weights (~80–130MB for the Phase 0 defaults) are all new. `scripts/check_deps.py` (currently checks `streamlit, rapidfuzz, jellyfish, pandas, openpyxl, numpy, phonetics, fuzzywuzzy, flask, fastapi, uvicorn`) and the `app.start` preflight both need these added. The WordNet corpus download needs the **same** auto-download treatment as the model weights, not just the model.

Serving requirements (Phase 0 deliverables):
- ONNX export of the chosen model.
- `lru_cache` on `semantic.resolve(query)`.
- `app.start` preflight auto-downloads model + corpus if missing.
- Embedding artifacts versioned by model+version in the filename (`exemplar_embeddings_<model>_<version>.npy`); `semantic.py` rebuilds on mismatch.
  *Status: DONE — `app.start` now auto-downloads the ONNX model (`scripts/export_embedding_model.py`) and the WordNet corpus (`enrichment.ensure_wordnet()`, with a direct-zip fallback when nltk's downloader silently no-ops) whenever the deps exist but the artifact is missing; skipped when `MERCHANT_TIER2_ENCODER=hash` is forced, never blocks startup. Exemplar vectors persist to `data/exemplar_embeddings_<encoder>_<fingerprint>.npy` + a JSON sidecar (row layout); `semantic._get_exemplar_vecs()` loads the artifact on cold start (a restart no longer re-encodes ~190 phrases) and rebuilds a fresh artifact when the exemplar fingerprint or encoder changes — stale/corrupt artifacts are detected and recomputed. Coverage in `tests/test_semantic_shadow.py` (round-trip, staleness, corruption).*

**Data is gitignored — say so, don't imply otherwise.** `data/` (where `exemplars.json` and `auto_pattern_manifest.json` live) is gitignored, same as `manual_aliases.json` today. "Auditable, diffable" holds at the application/review-UI layer and locally on disk — not in git history. State this explicitly rather than letting "auditable, diffable" imply version control.

**Shadow-log schema — an additive change, not "no schema change."** `calibration.record()`'s entry needs new fields (`tier2_prediction`, `matched_exemplar`) for Phase 1 shadow logging. Fine for an append-only JSONL log, but it is a schema change and should be described as one — v3's "no schema change" language applied only to `intents.json`'s pattern shape (§4), not to calibration logging, and the doc should make that scope explicit rather than reading as a blanket claim.

---

## 10. Integration point

One hook, not a new parallel path: inside `suggest_clarification()` (`engine.py:918`), which is the single function both `api.py:943` (`/api/task`) and the shared `analyze()` path call. Tier 2 runs here when Tier 1 (regex + existing `~semantic` fallback) is inconclusive, keeping the Rule Engine debug panel and both callers consistent with one implementation instead of two.

---

## 11. New files / module layout

```
merchant_intelligence/
  intent_golden.py          # DONE — Phase 0 held-out novel-phrasings set
                             #       (62 queries, 27 intents; the ongoing
                             #       intent golden set, §5/§7)
  tasks/
    semantic.py             # DONE — Tier-2 semantic match (Phase 1): masked-
                            #       fragment encode, calibrated 0-100 scoring
                            #       (§6), threshold+margin gates, shadow log.
                            #       HashingEncoder active; ONNXEncoder is the
                            #       documented seam for the §9 model export.
scripts/
  build_exemplars.py        # NEW — WordNet expansion, curation export, merges
                            #       approved synonyms into intents.json AND
                            #       regenerates vocab.py's _DEFAULT_* in
                            #       lockstep (§4), writes exemplars.json +
                            #       versioned .npy (§9)
  export_embedding_model.py # NEW — sentence-transformers -> ONNX export (§9)
  phase0_baseline.py        # DONE — baseline measurement harness (novelty
                            #       self-check + routed/clarify/misroute/miss;
                            #       --tier2 adds the Phase-1 preview)
tests/
  test_semantic_shadow.py   # DONE — offline Phase-1 tests (28 checks): knob,
                            #       exemplars, masking, resolve, off/shadow/
                            #       enabled hook behavior + §9 artifact round-trip
data/                       # gitignored — see §9
  exemplars.json
  exemplar_embeddings_<model>_<version>.npy
  exemplar_candidates.json
  auto_pattern_manifest.json
  phase0_baseline.json      # DONE — the Phase 0 baseline snapshot
  tier2_shadow.jsonl        # DONE — Phase-1 shadow decisions (append-only)
  tier2_shadow_review.jsonl # DONE — §7 spot-check labels (append-only, gitignored)
                             #       -> feeds calibration.fit_tier2() (Phase 3)
```

Query encoding for Tier 2: mask, then embed. Run the query through the existing identifier/merchant resolution machinery first, replace recognized spans with placeholder tokens (`[MERCHANT]`, `[TID]`, `[MX]`, `[PHONE]`...), then embed the masked string — keeps the vector focused on intent language regardless of whether the surrounding clause structure is one the parser has seen before.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| WordNet proposes domain-inappropriate synonyms | Human curation gate (§4) |
| Tier 1 build-time merge breaks the `[4h]` parity test | `build_exemplars.py` regenerates `vocab.py` defaults in the same run (§4) — not a "fix later" item |
| Auto pattern independently misfires via identity-keyed `is_task` branches | Named explicitly, not overclaimed as impossible (§4); weight 2 stays below every numeric gate, but identity-based routing is a stated residual risk |
| Embedding tier confidently misroutes | Margin gate + calibrated 0-100 scale (§6) + `~embedding:` tagging + `matched_exemplar` (§8) |
| Shadow-mode labels miss the auto-run confidence band | Explicit manual spot-check of that band required before Phase 2 (§7) — clarification labels alone are insufficient |
| Baseline investment case overstated | Phase 0 measures against the *existing* `~semantic` fallback, not stripped-down regex (§2, §7) |
| `golden.py` mistaken for available intent ground truth | Corrected: it isn't; Phase 0's set is the only intent golden set (§5) |
| Two competing learning loops writing to the same files | Phase B extends `feedback.py`'s existing `mine_patterns()` output rather than duplicating it (§5) |
| Gemma's non-standard tokenizer/ONNX path delays Phase 0 | Phase 0 defaults to MiniLM/bge-small; Gemma is an evaluated upgrade only (§9) |
| "Auditable, diffable" read as git-tracked | `data/` is gitignored, stated explicitly (§9) |
