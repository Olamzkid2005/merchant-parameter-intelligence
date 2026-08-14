"""
fuzzy.py — Shared fuzzy-matching helpers.

Backed by rapidfuzz (C-optimised) and jellyfish (phonetic) when available,
with pure-Python fallbacks so the package always works.

Helpers:
  - fuzzy_ratio(a, b)                  -> 0..1 string similarity (rapidfuzz.ratio)
  - token_sort_ratio(a, b)             -> 0..1 order-insensitive token similarity
  - levenshtein_similarity(a, b)       -> 0..1 edit-distance similarity
  - damerau_levenshtein_similarity(a,b)-> 0..1 transposition-aware edit distance
  - canonicalize(text)                 -> normalised name for consistent matching
  - phonetic_key(word)                 -> Metaphone key ("" if unavailable)
  - phonetic_similarity(a, b)          -> 0..1 phonetic key similarity
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List

try:
    from rapidfuzz import fuzz as _fuzz
    from rapidfuzz.distance import Levenshtein as _Levenshtein
    RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _fuzz = None
    _Levenshtein = None
    RAPIDFUZZ = False

try:
    from rapidfuzz.distance import DamerauLevenshtein as _Damerau
    HAS_DAMERAU = True
except ImportError:  # pragma: no cover
    _Damerau = None
    HAS_DAMERAU = False

try:
    import jellyfish as _jellyfish
    JELLYFISH = True
except ImportError:  # pragma: no cover
    _jellyfish = None
    JELLYFISH = False


# ── String similarity ──────────────────────────────────────────────────────

def fuzzy_ratio(a: str, b: str) -> float:
    """0..1 similarity between two strings (case-insensitive)."""
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if RAPIDFUZZ:
        return _fuzz.ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def token_sort_ratio(a: str, b: str) -> float:
    """0..1 similarity ignoring token order (e.g. 'PETER ANUCHA' vs 'ANUCHA PETER')."""
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if RAPIDFUZZ:
        return _fuzz.token_sort_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def token_set_ratio(a: str, b: str) -> float:
    """0..1 similarity tolerant of BOTH order and extra/subset tokens.

    'LAGOON WATERS' vs 'LAGOON WATER ENT' — token_sort_ratio punishes the
    missing WATERS token; token_set_ratio sees the shared set and scores the
    common core highly. Better than plain ratio for merchant names where the
    registry name carries extra tokens (branch, store, suffix).
    """
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if RAPIDFUZZ:
        return _fuzz.token_set_ratio(a, b) / 100.0
    # Pure-Python fallback: similarity of the shared token set vs each side.
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    if not inter:
        return 0.0
    base = SequenceMatcher(None, " ".join(inter), a).ratio()
    return max(base, SequenceMatcher(None, " ".join(inter), b).ratio())


def partial_ratio(a: str, b: str) -> float:
    """0..1 best-match alignment of the shorter string inside the longer one.

    'MEDPLUS' vs 'MEDPLUS PHARMACY SANGOTEDO' — partial_ratio finds the
    contained phrase and scores it highly, where plain ratio dilutes on the
    extra tokens.
    """
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if RAPIDFUZZ:
        return _fuzz.partial_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def levenshtein_similarity(a: str, b: str) -> float:
    """0..1 similarity derived from Levenshtein edit distance."""
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    if RAPIDFUZZ and _Levenshtein is not None:
        dist = _Levenshtein.distance(a, b)
    else:
        dist = _py_levenshtein(a, b)
    return max(0.0, 1.0 - (dist / max_len))


def damerau_levenshtein_similarity(a: str, b: str) -> float:
    """0..1 similarity using Damerau-Levenshtein (optimal string alignment).

    Counts a transposition ("INTERNMATIONAL" -> "INTERNATIONAL") as ONE edit
    instead of two, which is exactly the typo class that keeps appearing in
    the workbook (INTERNMATIONAL, MICROFINANACE, LIIMITED, OLWADAMS).
    """
    a, b = (a or "").upper(), (b or "").upper()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    if HAS_DAMERAU and _Damerau is not None:
        dist = _Damerau.distance(a, b)
    else:
        dist = _py_osa(a, b)
    return max(0.0, 1.0 - (dist / max_len))


def _py_osa(s1: str, s2: str) -> int:
    """Pure-Python optimal-string-alignment Damerau distance (fallback)."""
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    d = [[0] * (len(s2) + 1) for _ in range(len(s1) + 1)]
    for i in range(len(s1) + 1):
        d[i][0] = i
    for j in range(len(s2) + 1):
        d[0][j] = j
    for i in range(1, len(s1) + 1):
        for j in range(1, len(s2) + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and s1[i - 1] == s2[j - 2] and s1[i - 2] == s2[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _py_levenshtein(s1: str, s2: str) -> int:
    """Pure-Python Levenshtein distance (fallback when rapidfuzz missing)."""
    if len(s1) < len(s2):
        return _py_levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


# ── Phonetic matching (Metaphone) ──────────────────────────────────────────

def phonetic_key(word: str) -> str:
    """Return the Metaphone key for a word, or the upper-cased word as fallback.

    Returns "" for empty input. Metaphone (via jellyfish) is well suited to
    catching transliteration drift in merchant / person names.
    """
    w = (word or "").upper().strip()
    if not w:
        return ""
    if JELLYFISH:
        key = _jellyfish.metaphone(w)
        return key or ""
    return w


def phonetic_similarity(a: str, b: str) -> float:
    """0..1 similarity between two words using their Metaphone keys.

    Returns 0.0 if phonetic support is unavailable (caller should rely on
    normal fuzzy matching instead).
    """
    if not JELLYFISH:
        return 0.0
    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if ka in kb or kb in ka:
        # One key is a prefix/contained in the other -> strong transliteration sign
        return 0.85
    return SequenceMatcher(None, ka, kb).ratio()


def strip_diacritics(text: str) -> str:
    """Remove combining diacritical marks (NFKD), so accented Nigerian name
    spellings unify with plain ASCII: ẸBENEZER -> EBENEZER, ỌLÁ -> OLA,
    ṢEGUN -> SEGUN. Letters keep their base form (ñ -> n, ç -> c)."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def canonicalize(text: str) -> str:
    """Normalise a merchant/query string for consistent matching (Tier 2).

    Applied at BOTH ingest and query time so both sides agree:
      - uppercase
      - strip diacritics     (ẸBENEZER -> EBENEZER)
      - expand ampersands      (G&G  -> "G AND G")
      - expand abbreviations   (INT'L -> INTERNATIONAL, via config)
      - fix known typos        (MICROFINANACE -> MICROFINANCE, via config)
      - remove apostrophes     (E'SORAE -> ESORAE)
      - collapse punctuation / whitespace

    Generic words (LTD, LIMITED…) are NOT stripped here — tokenisation and
    bucket keys handle those separately — so the raw normalised string stays
    useful for full-name comparisons.
    """
    from . import config  # local import avoids any import-cycle risk
    t = strip_diacritics(text).upper().strip()
    t = t.replace("&", " AND ")
    for abbr, full in config.NAME_ABBREVIATIONS.items():
        t = re.sub(r"\b" + re.escape(abbr) + r"\b", full, t)
    # Known typos — word-boundary so "MICROFINANACE" in
    # "MONEYTRUST MICROFINANACE BANK" is rewritten but "LIIMITED" inside
    # "UNLIMITED" is left alone.
    for typo, fix in config.TYPO_FIXES.items():
        t = re.sub(r"\b" + re.escape(typo) + r"\b", fix, t)
    t = t.replace("'", "")  # E'SORAE -> ESORAE
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_email(email: str) -> str:
    """Lower-case, strip whitespace."""
    return (email or "").strip().lower()


def normalize_phone(phone: str) -> str:
    """Keep only digits; return '' if too short to be a real phone."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits if len(digits) >= 10 else ""


def normalize_code(value: str) -> str:
    """Normalise a code-like value (MX/TID/account) for exact linking."""
    return (value or "").strip().upper()


# ── Confusable-character handling ──────────────────────────────────────────
# Alphanumeric codes in this registry mix letters and digits that look alike —
# TID "2103O265" (letter O) is routinely typed as "21030265" (digit 0). The
# classic confusable pairs are 0/O, 1/I, 2/Z, 5/S, 8/B. Matching is DB-rooted:
# a confusable spelling is only ever accepted when the registry ACTUALLY
# stores that alternative form, so this can never invent matches.

# Bidirectional swap map — used ONLY to GENERATE alternative spellings
# ('21030265' -> '2103O265'). Both directions so either spelling can be
# produced from the other.
CONFUSABLE_MAP = {
    "0": "O", "O": "0",
    "1": "I", "I": "1",
    "2": "Z", "Z": "2",
    "5": "S", "S": "5",
    "8": "B", "B": "8",
}

# Canonical representative per confusable CLASS — used to COMPARE two
# spellings. Every member of a class maps to the same representative, so the
# keys of '21030265' and '2103O265' are equal. (A bidirectional map would
# swap rather than unify and never match.)
_CONFUSABLE_CANON = {
    "0": "O", "O": "O",
    "1": "I", "I": "I",
    "2": "Z", "Z": "Z",
    "5": "S", "S": "S",
    "8": "B", "B": "B",
}


def confusable_key(value: str) -> str:
    """Canonical form with confusable chars unified (0↔O, 1↔I, …).

    '21030265' and '2103O265' both map to '2103O265', so two values are
    confusable-equivalent iff their keys are equal.
    """
    t = (value or "").strip().upper()
    return "".join(_CONFUSABLE_CANON.get(c, c) for c in t)


def confusable_variants(value: str, max_variants: int = 24) -> List[str]:
    """Bounded set of confusable spellings of a code, original first.

    Widens identifier retrieval/resolution so a user typing a digit where the
    registry stores the look-alike letter (and vice versa) still finds the
    row. The alternative spelling is only accepted when the DB actually
    stores it, so no match is ever invented. Substitutions are applied one
    position at a time (plus a few doubles for codes with several look-alike
    chars); the list is capped so long codes can't explode.
    """
    t = (value or "").strip().upper()
    if not t:
        return [t]
    positions = [i for i, c in enumerate(t) if c in CONFUSABLE_MAP]
    variants = {t}
    for i in positions:
        variants.add(t[:i] + CONFUSABLE_MAP[t[i]] + t[i + 1:])
    if len(variants) < max_variants and len(positions) >= 2:
        for a in range(len(positions)):
            for b in range(a + 1, len(positions)):
                va = (t[:positions[a]] + CONFUSABLE_MAP[t[positions[a]]]
                      + t[positions[a] + 1:])
                vb = (va[:positions[b]] + CONFUSABLE_MAP[va[positions[b]]]
                      + va[positions[b] + 1:])
                variants.add(vb)
                if len(variants) >= max_variants:
                    break
            if len(variants) >= max_variants:
                break
    return sorted(variants)[:max_variants]


# ── Identifier format validation ──────────────────────────────────────────
# These guard substring identifier matches (score 90) so a junk digit string
# can't false-positive against an account/TID/BVN that merely contains it.
# Exact (string-identical) matches are never gated — the user typed exactly
# what is stored.

# CBN NUBAN check-digit weights (applied to the first 9 digits of a 10-digit
# account number; the 10th digit must equal the computed check digit).
_NUBAN_WEIGHTS = (3, 7, 3, 3, 7, 3, 3, 7, 3)


def is_valid_nuban(value) -> bool:
    """True when a 10-digit account number passes the CBN NUBAN checksum.

    '0123456789'-style junk that fails the check digit is rejected, so
    substring matches against account_number / static_acc_no are only granted
    for plausible account numbers.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 10:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], _NUBAN_WEIGHTS))
    check = (10 - (total % 10)) % 10
    return check == int(digits[9])


def is_valid_bvn(value) -> bool:
    """True when a value is a plausible BVN: exactly 11 digits starting with 2."""
    digits = re.sub(r"\D", "", str(value or ""))
    return len(digits) == 11 and digits.startswith("2")


def is_plausible_tid(value) -> bool:
    """True when a value reads like a terminal ID (>= 7 chars, mostly digits).

    A terminal ID is more like '21030173' or '2103O338' — a short value like
    '507' is not a TID and must not get substring-match credit.
    """
    s = str(value or "").strip()
    if len(s) < 7:
        return False
    digits = sum(c.isdigit() for c in s)
    return digits >= 6
