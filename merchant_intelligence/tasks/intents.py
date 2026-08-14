"""
intents.py — Weighted intent detection for the task engine.

_analyze (regex-weighted scores + confidence), detect_intents / detect_intent
(ranking + cross-intent disambiguation) and _countable_target. Depends only on
the shared vocabulary and parser.extract_segment.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .parser import extract_segment, parse_identifiers, split_clauses
from .vocab import (
    COMPILED_INTENT_PATTERNS, INTENT_FUZZY, INTENT_GRAPH, INTENT_KEYWORDS,
    INTENT_SLANG, NEGATION_MARKERS, WORKFLOW_STEPS, _lower, _normalize,
)

# Semantic fallback tuning: a keyword phrase must overlap the request this
# much before it counts as a soft pattern, and the boost is capped so regex
# hits always outrank it (0.75 -> 0, 0.93 -> 4.0).
_SEMANTIC_MIN_SIM = 0.75
_SEMANTIC_MAX_SCORE = 4.0

# Fuzzy-token guard for the offline semantic tier: a phrase token may match a
# request token within ONE Damerau edit (sub/insert/delete/transpose) only
# when both are >= 5 chars — short keywords ('tid', 'top', 'bank') are too
# noisy to trust one edit against, and identifier tokens ('MX141692') must
# never fuzzy-match a field word. A pure prefix/suffix extension ('county'
# vs 'count', 'estate' vs 'state') is a different word, not a typo, so those
# are rejected too. This mirrors spaCy's FUZZY matcher: typos classify,
# unrelated words don't.
_FUZZY_MIN_LEN = 5


# Damerau-Levenshtein distance with adjacent-transposition cost 1 ('emial' ->
# 'email' is ONE edit, a common typo). Full-matrix restricted algorithm — the
# strings here are short keywords, so a simple implementation beats a
# fiddly rolling-row one. Length-gate fast path: distance <= 1 is impossible
# when the lengths differ by more than 1.
def _damerau(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 99
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,            # deletion
                d[i][j - 1] + 1,            # insertion
                d[i - 1][j - 1] + cost,     # substitution
            )
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2]
                    and a[i - 2] == b[j - 1]):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)  # transposition
    return d[n][m]


def _analyze(
    text: str, exclude: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Weighted intent analysis: intent -> {score, confidence, matched}.

    score       weighted sum of matched patterns (repeat mentions capped at 2)
    confidence  0-100 = min(100, score * 12) — a strong phrase reaches ~95,
                a lone generic word stays in the 30s (won't create a task).
    matched     the actual patterns that fired (debug endpoint shows WHY);
                '~semantic:0.88' entries are offline fuzzy keyword hits.

    exclude     intents to ignore entirely (negation: '...but not the change
                history'). Excluded intents never score, so they also never
                trigger the cross-intent subsumption rules downstream.
    """
    low = _normalize(text)
    blocked = set(exclude or ())
    multi_word = len(low.split()) >= 2
    out: Dict[str, Dict[str, Any]] = {}
    for intent, pats in COMPILED_INTENT_PATTERNS.items():
        if intent in blocked:
            continue
        score = 0.0
        matched: List[str] = []
        for pat, weight in pats:
            count = len(pat.findall(low))
            if count:
                score += weight * min(count, 2)
                matched.append(pat.pattern)
        # Offline semantic fallback (per-intent toggle: INTENT_FUZZY, default
        # ON — set "fuzzy": false in intents.json / the Rule Engine toggle to
        # restrict this intent to exact regex patterns only).
        if score == 0 and multi_word and INTENT_FUZZY.get(intent, True):
            # Keywords act as SOFT fuzzy patterns. A paraphrase that misses
            # every regex but strongly overlaps a keyword phrase ('customer
            # mail' -> email) still classifies. Multi-word guard: a bare
            # identifier ('MX141692') is one token and must never be boosted
            # into an intent.
            sim, fuzzy = _phrase_similarity(
                low, INTENT_KEYWORDS.get(intent, []))
            if sim >= _SEMANTIC_MIN_SIM:
                score = round(min(_SEMANTIC_MAX_SCORE,
                                  (sim - _SEMANTIC_MIN_SIM) * 22), 1)
                # Distinguish typo hits from paraphrase hits so the Rule
                # Engine debug panel shows WHY ('~fuzzy:1.00' = typo'd
                # keyword, '~semantic:1.00' = paraphrase coverage).
                matched.append(
                    f"~fuzzy:{sim:.2f}" if fuzzy else f"~semantic:{sim:.2f}")
        if score > 0:
            out[intent] = {
                "score": round(score, 1),
                "confidence": min(100, int(score * 12)),
                "matched": matched,
            }
    return out


def _stem(tok: str) -> str:
    """Plural-lite normalisation for token equality. Only a trailing 's' is
    stripped (and never from a double-s word: 'address'/'adress' are singular
    forms, not plurals). 'accounts' -> account, 'phones' -> phone, but
    'address' stays address."""
    if tok.endswith("ss"):
        return tok
    return tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok


def _phrase_similarity(text: str,
                       phrases: List[str]) -> Tuple[float, bool]:
    """Keyword-phrase coverage score (0..1) — the offline 'semantic' fallback.

    Returns (best_sim, fuzzy_used) where fuzzy_used tells whether the WINNING
    phrase needed a within-one-edit match. Score = fraction of the phrase's
    tokens present in the request (order-tolerant, plural-tolerant:
    'accounts' counts for 'account'). Token equality (never substring) means
    'count' can't match inside 'account'. When a token misses exactly, a
    within-one-edit Damerau match ('sttic' -> static, 'emial' -> email,
    'paybles' -> payable) still counts — typo'd field words classify, and
    the flag lets the caller label the hit '~fuzzy' instead of '~semantic'.
    Pure stdlib.

    Candidate tokens are BOTH raw and stemmed request tokens, so a typo is
    matched against the form that makes it one edit: 'adress' vs 'address'
    (both double-s, unstemmed) and 'paybles' vs the stem of 'paybles' =
    'payble' -> 'payable'.
    """
    raw = _normalize(text).split()
    stems = {_stem(w) for w in raw}
    cands = set(raw) | stems
    best = 0.0
    best_fuzzy = False
    for phrase in phrases:
        # Normalize the phrase side too ('acct mgr' -> 'account manager') so
        # a slang request overlaps the canonical keyword phrase exactly.
        p = _normalize(phrase.strip())
        toks = [_stem(w) for w in p.split()]
        if not toks:
            continue
        hits = 0
        phrase_fuzzy = False
        for w in toks:
            if w in stems:
                hits += 1
                continue
            # Fuzzy fallback: within one Damerau edit of a request token,
            # with the length / extension / identifier guards above.
            if len(w) >= _FUZZY_MIN_LEN:
                for c in cands:
                    if len(c) < _FUZZY_MIN_LEN or any(ch.isdigit() for ch in c):
                        continue
                    if (w.startswith(c) or c.startswith(w)
                            or w.endswith(c) or c.endswith(w)):
                        continue  # word-form extension, not a typo
                    if _damerau(w, c) <= 1:
                        hits += 1
                        phrase_fuzzy = True
                        break
        sim = hits / len(toks)
        if sim > best:
            best = sim
            best_fuzzy = phrase_fuzzy
    return best, best_fuzzy


def _detect_negated_intents(text: str) -> List[str]:
    """Intents the user explicitly excluded ('...but not the change history').

    For each negation marker only the NEAREST intent keyword after it is
    excluded (position-wise, first hit wins) — a later positive clause
    ('...but not the change history, and also the email') is never swept in.
    The 'not a'/'not an' articles check just the word right after them, so
    'not an email' excludes email but 'not available in the email file' does
    not. Bare 'not'/'without' are absent from the markers, so presence
    filters ('stations without email') keep working.
    """
    low = _normalize(text)
    excluded: List[str] = []
    for marker in NEGATION_MARKERS:
        for m in re.finditer(r"\b" + re.escape(marker) + r"\b", low):
            window = low[m.end(): m.end() + 80]
            if marker in ("not a", "not an"):
                # The negated noun directly follows the article.
                head = (window.split()[0] if window.split() else "")
                for intent, kws in INTENT_KEYWORDS.items():
                    if intent not in excluded and any(kw in head for kw in kws):
                        excluded.append(intent)
                        break
                continue
            best_pos: Optional[int] = None
            best_intent: Optional[str] = None
            for intent, kws in INTENT_KEYWORDS.items():
                for kw in kws:
                    # Word-boundary match so a short keyword never misfires
                    # inside a longer word (e.g. 'count' inside 'account'),
                    # with an optional trailing plural (phone -> phones).
                    # The keyword side is normalised too, so a slang-only
                    # keyword ('deets') can be excluded by 'but not the deets'
                    # even though the request text expands first.
                    m = re.search(r"\b" + re.escape(_normalize(kw)) + r"s?\b",
                                  window)
                    pos = m.start() if m else -1
                    if pos != -1 and (best_pos is None or pos < best_pos):
                        best_pos, best_intent = pos, intent
            if best_intent is not None and best_intent not in excluded:
                excluded.append(best_intent)
    return excluded


def build_execution_plan(intents: List[str]) -> Dict[str, Any]:
    """Dependency-aware execution plan for the detected intents.

    Orders the pipelines and marks the sub-steps each resolves internally
    (static_account requires mxcode — the plan lists it as a requirement,
    never a duplicate pipeline). Returns:
      {"workflow": ["fetch_email", "fetch_phone"], "steps": [...]}
    """
    workflow: List[str] = []
    steps: List[Dict[str, Any]] = []
    for intent in intents:
        if intent == "resolve":
            continue
        step = WORKFLOW_STEPS.get(intent, f"run_{intent}")
        reqs = (INTENT_GRAPH.get(intent) or {}).get("requires", [])
        workflow.append(step)
        steps.append({
            "intent": intent,
            "step": step,
            "requires": [WORKFLOW_STEPS.get(r, f"run_{r}") for r in reqs],
            "resolved_internally": list(reqs),
            "produces": (INTENT_GRAPH.get(intent) or {}).get("produces", []),
        })
    return {"workflow": workflow, "steps": steps}


def detect_intents(
    text: str, exclude: Optional[Iterable[str]] = None,
) -> List[str]:
    """All intents expressed in the text, ranked (best first)."""
    scores = _analyze(text, exclude=exclude)
    if not scores:
        return ["resolve"]
    ranked = sorted(scores, key=lambda k: scores[k]["score"], reverse=True)
    # static_account implies mxcode as a sub-step (the pipeline resolves MX
    # codes internally) and already returns payable/alias/beneficiary columns
    # — drop the redundant chained intents so 'get the alias and payables
    # mapped to MX141692' routes to one pipeline, not a compound. account_name
    # / account_number ride along semantically whenever the text contains the
    # words account+name/account+number ("…static account and the beneficiary
    # name…"), so they are dropped when static wins the score.
    if "static_account" in ranked:
        for sub in ("mxcode", "payable", "alias", "beneficiary"):
            if sub in ranked:
                ranked = [i for i in ranked if i != sub]
        for sub in ("account_name", "account_number"):
            if sub in ranked and scores["static_account"]["score"] >= scores[sub]["score"]:
                ranked = [i for i in ranked if i != sub]
    # change_details covers the full change picture — profile's generic
    # "details"/"info" is the same phrasing, not a separate request.
    if "change_details" in ranked and "profile" in ranked:
        ranked = [i for i in ranked if i != "profile"]
    # A ranking request ("top 10", "most common", "per state") is a
    # group-by, never a count — 'how many merchants per state' wants the
    # per-state breakdown, not a single total. The ranking pipeline does the
    # grouping itself, so the field intent it ranks by ('top 10 banks') is
    # redundant and would only produce an empty merged compound.
    if "top" in ranked:
        ranked = [i for i in ranked if i != "count"]
        for f in ("bank", "account_name", "account_number", "payable", "alias",
                  "contact", "onboarded", "state", "source", "beneficiary",
                  "email", "phone", "mxcode", "address"):
            if f in ranked:
                ranked = [i for i in ranked if i != f]
    # Coverage ('which nnpc stations have no email') is the segment pipeline
    # with missing-filters — the field it names is the filter, not a separate
    # extraction, so the field intent is dropped.
    if "coverage" in ranked:
        for f in ("email", "phone", "address"):
            if f in ranked:
                ranked = [i for i in ranked if i != f]
    # count must clearly outrank a field intent ("get the phone number of X"
    # is phone, not count): a strong count signal ("how many"/"count") wins,
    # a weak one ("number of") loses to the field it refers to.
    if "count" in ranked:
        fields = {f for f in ("email", "phone", "mxcode", "address",
                              "static_account", "change_details") if f in ranked}
        if fields and scores["count"]["score"] <= max(scores[f]["score"] for f in fields):
            ranked = [i for i in ranked if i != "count"]
    # change_details must not ride along on 'account details' inside a static
    # account request ("the merchant's static account details and beneficiary"
    # is static_account, not a change-of-account request). But a genuine
    # compound request ("static account AND the change details of X") keeps
    # change_details — only weak substring signals (account/bank/address
    # old-new pairs) are dropped, never a strong "change of / change details".
    WEAK_CHANGE_PATTERNS = {
        r"\baccount details\b", r"\bold account\b", r"\bnew account\b",
        r"\bold bank\b", r"\bnew bank\b", r"\bold address\b",
        r"\bnew address\b",
    }
    if "static_account" in ranked and "change_details" in ranked:
        ch = scores["change_details"]
        # Semantic/fuzzy hits ('~semantic:0.88', '~fuzzy:0.92') are never
        # weak-change signals — a paraphrase or typo isn't an old/new pair.
        if any(m.startswith("~") for m in ch["matched"]):
            pass
        elif set(ch["matched"]) <= WEAK_CHANGE_PATTERNS:
            ranked = [i for i in ranked if i != "change_details"]
    return ranked


def detect_intent(text: str) -> str:
    """Primary intent (back-compat: single intent)."""
    return detect_intents(text)[0]


def _countable_target(text: str) -> bool:
    """Does a count request have something to count?

    'how many nnpc merchants' -> True ('how many'); 'count all nnpc merchants'
    -> True (segment fragment + field); 'count of monte cristo' -> False (no
    field word — a plain merchant name, not a counting request).
    """
    if re.search(r"\bhow many\b", _lower(text)):
        return True
    seg, fields = extract_segment(text)
    return bool(seg and fields)


def _merge_ids(target: Dict[str, List[str]], incoming: Dict[str, List[str]]) -> None:
    """Merge incoming identifier values into target (kind -> deduped list)."""
    for kind, vals in incoming.items():
        bucket = target.setdefault(kind, [])
        for v in vals:
            if v not in bucket:
                bucket.append(v)


def extract_clause_entities(
    text: str, known_ids: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Attach each intent to the identifiers named in its own clause.

    'get email for 2103O338 and phone for MX141692' ->
      [{'intent': 'email', 'identifiers': {'tid': ['2103O338']}},
       {'intent': 'phone', 'identifiers': {'mxcode': ['MX141692']}}]

    Only meaningful for compound requests with 2+ identifiers — a plain name
    like 'RUBELS AND ANGELS RESTAURANT' has no identifiers and is never
    split. An id-only trailing clause ('...and 2103O340') inherits the
    previous clause's intent so 'email for 2103O338 and 2103O340' keeps both
    ids on email; a leading id-only clause stays unattached (execute_task
    then falls back to the full set rather than dropping it).

    known_ids: identifiers already parsed from the WHOLE text (detect_task
    has them) — skips the duplicate full-text classifier pass. Per-clause
    parses still run, but only over each clause's own tokens.
    """
    ids = known_ids if known_ids is not None else parse_identifiers(text)
    if sum(len(v) for v in ids.values()) < 2:
        return []
    clauses: List[Dict[str, Any]] = []
    for clause in split_clauses(text):
        c_ids = parse_identifiers(clause)
        if not any(c_ids.values()):
            continue
        c_intent = detect_intent(clause)
        if c_intent == "resolve" and clauses:
            # Id-only trailing clause: inherits the previous clause's intent
            # ('email for 2103O338 and 2103O340' -> both ids on email).
            _merge_ids(clauses[-1]["identifiers"], c_ids)
            continue
        if c_intent == "resolve":
            continue  # leading id-only clause — stays in the full set
        target = next((c for c in clauses if c["intent"] == c_intent), None)
        if target:
            _merge_ids(target["identifiers"], c_ids)
        else:
            clauses.append({"intent": c_intent, "identifiers": c_ids})
    return clauses
