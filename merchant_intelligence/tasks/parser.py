"""
parser.py — Text -> structured entities for the task engine.

parse_identifiers / parse_named_identifiers (DB-rooted classifier),
extract_segment (collection fragments), extract_names (merchant names) and
extract_params (state / presence / limit filters), plus the private helpers
behind them. No DB access — the outputs are raw text structures that db.py /
pipelines.py consume.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from ..idclass import classify, classify_many
from .vocab import (
    ADDRESS_LOCALITY_WORDS, ADDRESS_TYPE_WORDS,
    ID_KINDS, INSTRUCTION_WORDS, KEY_MERCHANT_ROOTS, LIGHT_NAME_STOPS,
    MAX_RESULT_LIMIT, NAME_ANCHORS, NAME_STOP_WORDS, NIGERIA_STATES,
    PRESENCE_PATTERNS, SAFE_SHORT_STATES, SEGMENT_FIELDS, SEGMENT_STOP_WORDS,
    _lower, _normalize, _whole_word_re,
)

def _match_fields(low: str) -> List[str]:
    """SEGMENT_FIELDS whose keywords appear as whole words in the text."""
    return [f for f, kws in SEGMENT_FIELDS.items()
            if any(_whole_word_re(kw).search(low) for kw in kws)]


def _has_field_word(low: str) -> bool:
    """True if any SEGMENT_FIELDS keyword appears as a whole word."""
    return bool(_match_fields(low))


def _key_merchant_in_text(low: str) -> bool:
    """True when a key merchant root appears as a whole word in the text.

    Matches the bare root AND multi-word roots ("JUST CHIPS"); the
    root-in-name case ("SPAR LEKKI" starts with SPAR) is handled by
    _match_key_merchant, which the engine uses on the EXTRACTED name.
    Also typo-tolerant: 'all adide stores in lagos' still counts as a key
    merchant collection request (ADIDE ~ ADDIDE).
    """
    for root in KEY_MERCHANT_ROOTS:
        if _whole_word_re(root.lower()).search(low):
            return True
    for w in re.findall(r"[a-z]{5,}", low):
        if _key_typo_root(w):
            return True
    return False


# Levenshtein distance with a length-gate fast path (a distance <= 1 is
# impossible when the lengths differ by more than 1).
def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _key_typo_root(word: str) -> Optional[str]:
    """A single-word key root within one edit of `word` ('' when none).

    Both sides must be >= 5 chars — short roots (SPAR, NNPC) are exact-only
    so 'SPARE PARTS' can never fuzzy-hit SPAR, and 4-letter words are too
    noisy to trust one edit against. Multi-word roots ("JUST CHIPS") are
    exact-only. 'MEDPLUZ' -> MEDPLUS, 'ADIDE' -> ADDIDE, 'CASCADE' ->
    CASCADES.
    """
    w = (word or "").strip().upper()
    if len(w) < 5:
        return None
    for root in KEY_MERCHANT_ROOTS:
        if " " in root or len(root) < 5:
            continue
        if _edit_distance(w, root) <= 1:
            return root
    return None


# Separators that END a key-merchant root in a longer name: a space
# ('BOKKU MART ILAJE'), a hyphen/en-dash as the DB actually stores branches
# ('BOKKU MART- ILAJE AJAH', 'LAGOON WATERS LTD -NNPC.'), a slash or an
# ampersand. 'BOKKU MARTEL' must NOT count — only real separators end a root.
_KM_BOUNDARY_CHARS = (" ", "-", "\u2013", "\u2014", "/", "&")


def _key_root_boundary(name: str, root: str) -> bool:
    """True when `name` starts with `root` followed by a boundary separator
    (or is exactly the root). 'BOKKU MART- ILAJE AJAH' starts with
    'BOKKU MART' + '-'; 'BOKKU MARTEL' never counts."""
    n = (name or "").strip().upper()
    if n == root:
        return True
    if not n.startswith(root):
        return False
    return n[len(root):len(root) + 1] in _KM_BOUNDARY_CHARS


def key_merchant_matches(name: str) -> List[str]:
    """Key-merchant roots an extracted merchant name belongs to ([] if none).

    'MEDPLUS' == root, 'MEDPLUS PHARMACY' starts with 'MEDPLUS ', 'MEDPLUZ'
    is within one edit of MEDPLUS — every branch/chain row of a key merchant
    routes together, and a typo'd request still resolves to the real family.
    Branch separators follow the DB's storage ('BOKKU MART- ILAJE AJAH'
    counts as a BOKKU MART branch).
    """
    n = (name or "").strip().upper()
    if not n:
        return []
    exact = [r for r in KEY_MERCHANT_ROOTS if _key_root_boundary(n, r)]
    if exact:
        # A specific family variant ("CASCADES LUXE") already implies the
        # bare root ("CASCADES") — drop the redundant prefix so the UI
        # reports the exact variant that matched, and roots[0] (engine
        # canonicalisation) picks the most specific root.
        return [r for r in exact
                if not any(r != s and _key_root_boundary(s, r)
                           for s in exact)]
    first = n.split()[0]
    root = _key_typo_root(first)
    return [root] if root else []


def _match_key_merchant(name: str) -> bool:
    """True when an extracted merchant name belongs to a key merchant family
    (exact, prefix, or within one edit of a single-word root)."""
    return bool(key_merchant_matches(name))


def _looks_like_segment(text: str) -> bool:
    """Collection request detection: collective marker + field word.

    'get me all the addresses of all nnpc stations' -> True (marker 'all the',
    field 'addresses', instruction 'get'). 'ALL STAR STORES' -> False (bare
    'all' + no instruction verb — stays a normal merchant search). A weak
    'all' also qualifies when a KEY merchant root is named ('all addide
    stores in lagos') — the root makes the collection intent unambiguous,
    while 'ALL STAR STORES' has no such root.
    """
    low = re.sub(r"\s+", " ", _normalize(text))
    if not _has_field_word(low):
        return False
    strong = any(_whole_word_re(m).search(low)
                 for m in ("all the", "list of", "list all", "every", "each", "all of"))
    weak_all = bool(_whole_word_re("all").search(low))
    has_instr = any(w in low for w in INSTRUCTION_WORDS)
    return strong or (weak_all and (has_instr or _key_merchant_in_text(low)))


def extract_segment(text: str) -> Tuple[str, List[str]]:
    """Pull the segment fragment and requested field(s) from a collection
    request: 'get me all the addresses of all nnpc stations' ->
    ('NNPC', ['address']). Returns ('', []) when nothing meaningful remains.
    """
    low = re.sub(r"\s+", " ", _normalize(text))
    fields = _match_fields(low)
    # "stations/outlets/stores" are the NOUN of the request, not a requested
    # field — when a concrete field (address/email/phone/…) was asked for,
    # 'merchant' was only picked up from that noun. Drop it so the summary
    # reads 'address' not 'address + merchant'.
    if any(f != "merchant" for f in fields):
        fields = [f for f in fields if f != "merchant"]
    field_words = {w for kws in SEGMENT_FIELDS.values() for w in kws}
    stop = SEGMENT_STOP_WORDS | {w.lower() for w in field_words}
    kept: List[str] = []
    for w in re.findall(r"[A-Za-z0-9]+", text):
        if w.lower() not in stop and len(w) >= 2:
            kept.append(w)
    seen: set = set()
    uniq = []
    for w in kept:
        u = w.upper()
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return " ".join(uniq), fields


_IDENTIFIER_TOKEN_RE = re.compile(r"\S+")


# Instruction-line detection: whole-word, not substring. A bare
# `w in low` check would treat "RELIABLE PHONES AND GADGET" as an
# instruction line because 'get' is a substring of 'GADGET' — and drop a
# real merchant name from a pasted list.
def _line_has_instruction(line: str) -> bool:
    low = _lower(line)
    return any(_whole_word_re(w).search(low) for w in INSTRUCTION_WORDS)


def looks_like_address(text: str) -> bool:
    """True when a pasted line reads as an ADDRESS, not a merchant name.

    Requires road-type vocabulary (ROAD/STREET/PLAZA/MALL/ESTATE/PLOT/…) AND
    a locality (state/city/area: LAGOS, LEKKI, IKEJA, …) — or two road-type
    words when no locality is named ('PLOT 5, BLOCK C, ADMIRALTY WAY').
    Merchant names like 'MEDPLUS PHARMACY' or 'BOKKU MART' contain neither,
    so they never misroute.
    """
    toks = set(re.findall(r"[A-Z0-9]+", (text or "").upper()))
    type_hits = toks & ADDRESS_TYPE_WORDS
    locality_hits = toks & ADDRESS_LOCALITY_WORDS
    if not type_hits:
        # No road-type word, but TWO distinct localities ('LAGOS ISLAND',
        # 'LEKKI LAGOS') read as a place, not a merchant. This catches
        # 'MEDPLUS MARINA LAGOS ISLAND, LAGOS STATE' where the extractor
        # strips 'STATE' as a stop word.
        return len(locality_hits) >= 2
    if locality_hits:
        return True
    return len(type_hits) >= 2


# When an instruction line in a pasted list still carries a trailing name
# ("…from parameter file IBRAHIM. BABAZAKI - NNPC"), keep that tail as a
# name instead of discarding the whole line. Markers are the phrases that
# introduce a following name/list in real requests.
_INSTR_LINE_NAME_RE = re.compile(
    r"(?:parameter file|merchant file|the file|the list|below|following)\s+"
    r"(.+?)\s*$", re.IGNORECASE)


# Single-word fragments left after stripping (FOR / OF / THE / SHOWN / …)
# are instruction filler, never a merchant name — a trailing name must be
# at least two real words, or one clearly non-filler word ("MEDPLUS").
_FILLER_SINGLE = frozenset({
    "FOR", "OF", "THE", "AND", "LIST", "BELOW", "FOLLOWING", "SHOWS",
    "SHOW", "SHOWN", "ABOVE", "IN", "ON", "FROM", "WITH", "ARE", "IS",
    "THIS", "THESE", "THOSE", "ME", "MY", "OUR", "THEIR", "FILE", "FILES",
    "HAVE", "HAS", "SEE", "SHOWING",
})


def _instruction_line_name(line: str) -> str:
    """Trailing merchant name on an instruction line ('' when there is none).

    'pls get the codes from parameter file IBRAHIM. BABAZAKI - NNPC' ->
    'IBRAHIM BABAZAKI NNPC'.
    """
    m = _INSTR_LINE_NAME_RE.search(line)
    if not m:
        # 'get me the tids for BRITISH INTERNATIONAL SCHOOL ROAD, LEKKI,
        # LAGOS' — the tail after the last preposition anchor is a full
        # ADDRESS, not a merchant name. Keep it as a name so the address
        # pipeline can match it (previously the whole line was dropped).
        anchored = _anchored_name(line)
        if anchored and looks_like_address(anchored):
            return anchored
        return ""
    tail = m.group(1)
    anchored = _anchored_name(tail)
    if anchored:
        words = anchored.split()
    else:
        tail = tail.replace("&", " AND ")
        words = [w for w in re.findall(r"[A-Z0-9']+", tail)
                 if w not in NAME_STOP_WORDS and len(w) >= 2]
    if len(words) >= 2:
        return " ".join(words)
    if len(words) == 1 and words[0] not in _FILLER_SINGLE:
        return words[0]
    return ""


# Identifier labels glued to their value with a hyphen/colon ('MXCODE-MX77826',
# 'TID:2103O338', 'PHONE-08000000000', 'EMAIL-a@b.com'). The tokenizer splits on
# whitespace only, so without this the whole glued token fails the DB classifier
# and the identifier silently vanishes into the name text. Only the label prefix
# is split off — the right side still has to classify as a real registry value
# before it becomes an identifier, so a merchant name like 'NO-LIMIT STORES'
# (label 'NO' + 'LIMIT') can never fabricate one.
_LABELED_IDENT_RE = re.compile(
    r"^(?:(?:MX\s*CODE|MX\s*CODES|MX|TID|TIDS|TERMINAL\s*ID|TERMINAL\s*IDS|"
    r"PHONE|PHONES|TELEPHONE|TEL|EMAIL|E[- ]?MAIL|ACCOUNT|ACCT|STATIC|"
    r"PAYABLE|PAYABLES|ALIAS|ALIASES|MID|BVN|CODE|CODES|NUMBER|NUMBERS|"
    r"ID|IDS|NO)\s*[-:]\s*)(.+)$",
    re.IGNORECASE)


# Referential phrasing: "the above merchant", "the previous request", "per
# above" — the entity comes from a PREVIOUS request, not this text. The engine
# must not extract "ABOVE MERCHANT" as a merchant name; the API layer resolves
# the reference against the last remembered context instead.
_REFERENCE_RE = re.compile(
    r"\b(?:the|this|that)\s+above(?:[- ](?:mentioned|named))?\b"
    r"|\babove[- ](?:mentioned|named)\b"
    r"|\b(?:per|as|see)\s+above\b"
    r"|\b(?:merchant|merchants)\s+above\b"
    r"|\bthe\s+(?:previous|prior|last)\b"
    r"|\b(?:previous|prior|earlier|last)\s+(?:request|query|search|batch|"
    r"list|merchant|merchants|one)\b",
    re.IGNORECASE)


def extract_reference(text: str) -> bool:
    """True when the request refers to an entity from a previous request
    ('the above merchant', 'the previous request', 'per above')."""
    return bool(_REFERENCE_RE.search(text or ""))


def _identifier_tokens(text: str) -> List[str]:
    """Candidate identifier tokens: whitespace / comma / semicolon delimited.

    '2103O338 FELIX OKONMAH' -> two tokens; 'MX184380,2103O338' -> two
    tokens (comma-separated lists must not merge into one unclassifiable
    token); '5180857349; 2800158' -> two tokens.

    Attached punctuation is stripped from both ends so 'MX183639.' / '2103O338:'
    / '(MX141692)' (as they appear mid-sentence or after a colon) still
    classify as the identifier they wrap.
    """
    tokens = []
    for m in _IDENTIFIER_TOKEN_RE.finditer(text or ""):
        for piece in re.split(r"[,;]", m.group(0)):
            tok = piece.strip().strip(".,:;!?()[]{}'\"")
            if not tok:
                continue
            lm = _LABELED_IDENT_RE.match(tok)
            if lm:
                # 'MXCODE-MX77826' -> 'MX77826' (the label is request-language;
                # the value still has to classify against the registry). Only
                # split when the value LOOKS like an identifier (>=4 chars with
                # a digit, or an email) — 'NO-LIMIT' / 'CODE-STORE' / 'ID-BOX'
                # (merchant-name words) never split, so a coincidental DB value
                # on the right side can't corrupt a hyphenated name.
                val = lm.group(1).strip(".,:;!?()[]{}'\"")
                if len(val) >= 4 and (any(ch.isdigit() for ch in val)
                                      or "@" in val):
                    tok = val
            if tok:
                tokens.append(tok)
    return tokens


def parse_identifiers(text: str) -> Dict[str, List[str]]:
    """Extract structured identifiers from free text using the DB classifier.

    Kinds: tid, mxcode, phone, email, account (10-digit), static (static
    account number), payable, bvn, mid (2ISW…), alias. A token the registry
    stores in two columns lands in both lists (resolution tries all columns).
    """
    tokens = _identifier_tokens(text)
    out = classify_many(tokens)
    found: Dict[str, List[str]] = {k: [] for k in ID_KINDS}
    for kind, vals in out.items():
        found.setdefault(kind, []).extend(vals)
    return found


def parse_named_identifiers(text: str) -> List[Dict[str, str]]:
    """Lines like '2103O338  FELIX OKONMAH' -> [{'id': '2103O338', 'name': 'FELIX OKONMAH'}].

    The FIRST token must classify as a known identifier kind; the remainder
    of the line is treated as the user-provided merchant name (used for
    cross-checking the registry, feature #7). Lines that read as instructions
    ("get the static account for MX183639") or whose identifier is not the
    leading token are ignored — otherwise the instruction wording would be
    captured as a "name" and trigger false name_mismatch statuses.
    """
    named = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ("\t" not in line and len(line.split()) < 2):
            continue
        if _line_has_instruction(line):
            continue
        tokens = line.split()
        if not classify(tokens[0]):
            continue
        rest = [t for t in tokens[1:]
                if t.upper() not in NAME_STOP_WORDS]
        name = " ".join(rest).strip()
        if name:
            named.append({"id": tokens[0], "name": name})
    return named


# ── Request parameters: state filter / presence filter / limit (v2) ──────
# canonical -> aliases. Text detection only trusts long aliases (+ LA/FCT) so
# a 2-letter abbreviation like "AN" (Anambra) can never false-positive on the
# word "an". SQL matching uses every alias against the state column.
def _state_in_text(low: str) -> str:
    """Return the canonical Nigerian state named in the text ('' if none)."""
    for canon, aliases in NIGERIA_STATES.items():
        for a in aliases:
            if len(a) < 4 and a not in SAFE_SHORT_STATES:
                continue
            if _whole_word_re(a.lower()).search(low):
                return canon
    return ""


# Missing-field phrases for the coverage intent: "without email", "no
# phone", "has no address", "missing email", "lacking email" -> the field
# that must be EMPTY in the result rows.
# Group 1 is the first field after the marker; group 2 is an optional
# trailing 'or <field>' so 'no email or phone' captures BOTH.
_MISSING_FIELD_RE = re.compile(
    r"\b(?:without|with no|has no|have no|missing|lacking|no)\s+"
    r"(?:an\s+|a\s+)?(e[- ]?mail|phones?|phone numbers?|telephones?|"
    r"mobile numbers?|addresses?|locations?)"
    r"(?:\s+or\s+(?:an\s+|a\s+)?(e[- ]?mail|phones?|phone numbers?|"
    r"telephones?|mobile numbers?|addresses?|locations?))?\b")


def _missing_fields(low: str) -> List[str]:
    """Fields the request says must be ABSENT ('no email or phone')."""
    out: List[str] = []
    for m in _MISSING_FIELD_RE.finditer(low):
        for gi in (1, 2):
            w = (m.group(gi) or "").lower()
            if w in ("e-mail", "email", "emails"):
                f = "email"
            elif w.startswith(("phone", "telephone", "mobile")):
                f = "phone"
            elif w.startswith(("address", "location")):
                f = "address"
            else:
                continue
            if f not in out:
                out.append(f)
    return out


def extract_params(text: str) -> Dict[str, Any]:
    """Pull filters & limits from a request: state, has[], missing[], limit.

    'get me all the addresses of all nnpc stations in lagos with email'
    -> {'state': 'LAGOS', 'has': ['email'], 'limit': None}.
    'which nnpc stations have no email or phone'
    -> {'state': '', 'has': [], 'missing': ['email', 'phone'], 'limit': None}.
    """
    low = re.sub(r"\s+", " ", _lower(text))
    params: Dict[str, Any] = {"state": _state_in_text(low), "has": [],
                              "missing": [], "limit": None}
    for field, pats in PRESENCE_PATTERNS.items():
        if any(re.search(p, low) for p in pats):
            params["has"].append(field)
    params["missing"] = _missing_fields(low)
    m = re.search(r"\b(?:top|first|last)\s+(\d{1,4})\b", low)
    if not m:
        m = re.search(r"\b(\d{1,4})\s+(?:records?|results?|rows?|merchants?|entries)\b", low)
    if m:
        params["limit"] = min(int(m.group(1)), MAX_RESULT_LIMIT)
    return params


# ── Compare pair extraction: 'compare LAGOON WATERS vs ARTEE INDUSTRIES'
# -> ['LAGOON WATERS', 'ARTEE INDUSTRIES']. Splits on vs/versus/against/
# and/with/between, then strips stop words from each side so instruction
# words never leak into a compared name. Identifiers are removed first — an
# identifier pair is handled by the compare pipeline directly.
COMPARE_SEP_RE = re.compile(
    r"\b(?:vs\.?|versus|against|compared to|with|and|between)\b", re.IGNORECASE)


def extract_compare_pair(text: str) -> List[str]:
    """The two sides of a compare request, or [] when fewer than two remain.

    'compare LAGOON WATERS vs ARTEE INDUSTRIES' ->
    ['LAGOON WATERS', 'ARTEE INDUSTRIES']; 'MX141692 vs MX183639' -> []
    (identifiers are stripped here and resolved by the compare pipeline).
    """
    t = (text or "").upper()
    for _kind, vals in parse_identifiers(t).items():
        for v in vals:
            t = t.replace(v.upper(), " ")
    cleaned: List[str] = []
    for part in COMPARE_SEP_RE.split(t):
        words = [w for w in re.findall(r"[A-Z0-9]+", part)
                 if w not in NAME_STOP_WORDS and len(w) >= 2]
        c = " ".join(words)
        if c and c not in cleaned:
            cleaned.append(c)
    return cleaned[:2]


# ── Clause-level extraction: attach each intent to its own identifier ────
# 'get email for 2103O338 and phone for MX141692' splits into two intent
# clauses, each owning its own identifier. Splitting is only ever applied by
# extract_clause_entities, which guards on the identifier count — plain names
# like 'RUBELS AND ANGELS RESTAURANT' (0 identifiers) are never split.
_CLAUSE_SPLIT_RE = re.compile(r"\b(?:and|then|also|plus)\b|[,;]", re.IGNORECASE)


def split_clauses(text: str) -> List[str]:
    """Split a request into intent clauses at conjunction boundaries.

    'get email for 2103O338 and phone for MX141692' ->
    ['get email for 2103O338', 'phone for MX141692'].
    """
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "") if c.strip()]


# Extra words stripped from a segment fragment (intent vocabulary, limit
# words, filler) — see detect_task's segment cleanup.
def _anchored_name(line: str) -> str:
    """The phrase after the LAST preposition anchor in a line ('' if none)."""
    low = _lower(line)
    best = -1
    for anchor in NAME_ANCHORS:
        for m in _whole_word_re(anchor).finditer(low):
            if m.end() > best:
                best = m.end()
    if best < 0:
        return ""
    words = [w for w in re.findall(r"[A-Z0-9']+", line[best:].upper())
             if w not in LIGHT_NAME_STOPS and len(w) >= 2]
    return " ".join(words)


def extract_names(text: str) -> List[str]:
    """Extract merchant name(s) from a NAME-ONLY request (v2).

    Strategy per line: (1) anchored — the phrase after "on/of/for/about/…"
    with only generic words stripped, so "profile of FIRST BANK" keeps the
    full name; (2) fallback — strip every stop word from the line. Line-aware
    so a pasted list ("LAGOON WATERS\nARTEE INDUSTRIES\nget the emails for
    these") yields one name per data line. Only used when the request has NO
    identifiers, so false positives can never misroute an identifier search.
    """
    t = (text or "").upper()
    for kind, vals in parse_identifiers(t).items():
        for v in vals:
            t = t.replace(v.upper(), " ")
    is_multi = len(t.splitlines()) > 1
    names = []
    for line in t.splitlines():
        # In a multi-line paste the instruction line is boilerplate, not a
        # name ("get the emails for these" -> 'THESE' would be garbage) —
        # mirror parse_named_identifiers' guard. Single-line requests are the
        # request itself and keep anchored extraction. A trailing name on the
        # instruction line ("…from parameter file IBRAHIM. BABAZAKI - NNPC")
        # is still captured so it never silently vanishes.
        if is_multi and _line_has_instruction(line):
            tail = _instruction_line_name(line)
            if tail:
                names.append(tail)
            continue
        anchored = _anchored_name(line)
        if anchored:
            names.append(anchored)
            continue
        line = line.replace("&", " AND ")
        words = [w for w in re.findall(r"[A-Z0-9']+", line)
                 if w not in NAME_STOP_WORDS and len(w) >= 2]
        if words:
            names.append(" ".join(words))
    return names
