"""
idclass.py — DB-rooted identifier classification.

Classifies a pasted token (TID, MX code, payable code, account number, BVN,
MID, alias, static account number, phone, email) by asking the registry
itself what the value looks like, instead of trusting hand-written regexes.

Why DB-first
------------
The identifier columns are dirty and overlapping. For example:
  - `payable_code` contains 7-digit codes, `Default_Payable_MX…`, `MX…_MERCHANT_APP`
    AND leaked contact names ("CONTACT NAME", "CALEB BAALE") and odd values.
  - `static_acc_no` and `account_number` are BOTH 10-digit numbers, but the
    actual value sets have ZERO overlap — only the DB can tell them apart.
  - `MX44117` is a valid value in BOTH `mxcode` and `payable_code`.

So the classifier:
  1. Builds an in-memory index token -> {columns} from DISTINCT column values.
  2. For a pasted token, looks it up in the index first (the DB is truth).
  3. Falls back to shape rules only for values NOT in the registry (new
     codes, typos) — the shape rules are derived from the real column
     distributions observed in this workbook.
  4. Caches the index, invalidated by the DB file's mtime so a rebuild is
     picked up automatically.
"""
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set

from . import config

logger = logging.getLogger(__name__)

# Column -> public kind name. Priority order matters for ambiguous tokens that
# legitimately appear in more than one column (e.g. MX… in mxcode + payable).
COLUMN_KINDS = [
    ("mxcode", "mxcode"),
    ("tid", "tid"),
    ("phone", "phone"),
    ("email", "email"),
    ("static_acc_no", "static"),
    ("account_number", "account"),
    ("bvn", "bvn"),
    ("merchant_id", "mid"),
    ("alias", "alias"),
    ("payable_code", "payable"),
]

# Shapes observed in THIS workbook's columns (derived from the data, not
# guessed) — used only when a token is not found in the registry index.
# Order: most specific first so the first match wins.
SHAPE_RULES = [
    (re.compile(r"^Default[_-]?Payable[_-]?MX\d+$", re.I), ["payable"]),
    (re.compile(r"^MX\d+_[A-Z_]+$", re.I), ["payable"]),
    (re.compile(r"^MX\d{4,8}$", re.I), ["mxcode"]),
    (re.compile(r"^2ISW[A-Z0-9]{11}$"), ["mid"]),      # 15-char merchant id
    (re.compile(r"^\d{4}[A-Z]\d{3}$", re.I), ["tid"]),  # 2103O338
    (re.compile(r"^2ISW[A-Z0-9]{4,6}$", re.I), ["tid"]),  # 2ISW313A / 2ISW3255
    (re.compile(r"^\d{8}$"), ["tid"]),  # 20443111 (384 real rows: SHOPRITE etc.)
    (re.compile(r"^\d{12}$"), ["bvn"]),
    (re.compile(r"^\d{11}$"), ["bvn", "phone"]),       # 11-digit: BVN or local phone
    (re.compile(r"^(?:\+?234|0)[789]\d{9}$"), ["phone"]),
    (re.compile(r"^\d{10}$"), ["static", "account"]),  # ambiguous by shape alone
    (re.compile(r"^\d{7}$"), ["payable"]),
    (re.compile(r"^\d{6}$"), ["alias"]),
    (re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I), ["email"]),
]

class IdentifierClassifier:
    """Classifies identifier tokens via a DB-membership index + shape rules."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._index: Dict[str, Set[str]] = {}
        self._built_mtime: Optional[float] = None

    # ── index ─────────────────────────────────────────────────────────────
    def _needs_rebuild(self) -> bool:
        try:
            mtime = os.path.getmtime(self.db_path)
        except OSError:
            return bool(self._index)
        return mtime != self._built_mtime

    def _build_index(self):
        """token -> set of column-kind names where that exact value appears."""
        index: Dict[str, Set[str]] = {}
        try:
            mtime = os.path.getmtime(self.db_path)
        except OSError:
            mtime = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            for col, kind in COLUMN_KINDS:
                try:
                    for (v,) in c.execute(
                        f"SELECT DISTINCT {col} FROM merchants "
                        f"WHERE {col} IS NOT NULL AND TRIM({col}) != ''"
                    ):
                        v = str(v).strip().upper()
                        if v and len(v) <= 64:
                            index.setdefault(v, set()).add(kind)
                except sqlite3.Error as exc:
                    logger.warning("idclass: could not read column %s: %s", col, exc)
            conn.close()
        except Exception as exc:
            logger.warning("idclass: index build failed (%s) — shape fallback only", exc)
        self._index = index
        self._built_mtime = mtime
        logger.debug("idclass: index has %d distinct identifier tokens", len(index))

    def classify(self, token: str) -> List[str]:
        """Return the identifier kind(s) for a token, best-first.

        Delegates to inspect() so the fast path and the debug breakdown can
        never drift apart. DB membership wins; shape rules are the fallback
        for values the registry has never seen. Never raises.
        """
        return self.inspect(token)["kinds"]

    def kinds_in_db(self) -> Dict[str, int]:
        """Count of distinct tokens per kind (diagnostics / tests)."""
        if self._needs_rebuild():
            self._build_index()
        counts = {kind: 0 for _, kind in COLUMN_KINDS}
        for cols in self._index.values():
            for k in cols:
                counts[k] += 1
        return counts

    def index_stats(self) -> Dict[str, Any]:
        """Diagnostics about the membership index itself (debug endpoint)."""
        if self._needs_rebuild():
            self._build_index()
        return {
            "db_path": self.db_path,
            "distinct_tokens": len(self._index),
            "kinds_in_db": self.kinds_in_db(),
            "indexed": bool(self._index),
        }

    def inspect(self, token: str) -> Dict[str, Any]:
        """Per-token breakdown for the debug endpoint — show WHY a value was
        classified the way it was, grounded in the DB index.

        Returns:
          token         original value
          normalized    upper-cased trimmed form
          kinds         identifier kind(s) assigned (best-first)
          primary       first (highest-priority) kind
          source        'db_membership' | 'shape_rule' | 'rejected' | 'unknown'
          in_db_columns the actual registry columns storing this value
                        (empty for shape-rule/unknown hits)
          shape_rule    the shape pattern that matched (shape-rule hits only)
          reason        human explanation for rejected/unknown values
        """
        t = (token or "").strip().upper()
        base = {"token": token, "normalized": t}
        if not t:
            return {**base, "kinds": [], "primary": "", "source": "empty",
                    "in_db_columns": [], "shape_rule": None, "reason": "blank"}
        if len(t) > 64:
            return {**base, "kinds": [], "primary": "", "source": "rejected",
                    "in_db_columns": [], "shape_rule": None,
                    "reason": "longer than 64 chars — not a registry identifier"}
        if len(t) < 4:
            # DB-grounded floor: every real identifier in this registry is at
            # least 4 chars. Values shorter than that are leaked fragments
            # ('2', '0', 'Y', 'DR', 'MID', '000', '011') that the dirty columns
            # store — trusting them turns a pasted merchant-name list into an
            # identifier lookup for the literal '2'.
            return {**base, "kinds": [], "primary": "", "source": "rejected",
                    "in_db_columns": [], "shape_rule": None,
                    "reason": "shorter than 4 chars — not a real registry identifier"}
        if not any(ch.isdigit() for ch in t) and "@" not in t:
            return {**base, "kinds": [], "primary": "", "source": "rejected",
                    "in_db_columns": [], "shape_rule": None,
                    "reason": "no digit or '@' — every real identifier in this registry contains one"}
        if self._needs_rebuild():
            self._build_index()
        cols = self._index.get(t)
        if cols:
            rank = {kind: i for i, (_, kind) in enumerate(COLUMN_KINDS)}
            kinds = sorted(cols, key=lambda k: rank.get(k, 99))
            return {**base, "kinds": kinds, "primary": kinds[0] if kinds else "",
                    "source": "db_membership", "in_db_columns": sorted(cols),
                    "shape_rule": None, "reason": "value exists in the registry"}
        for pattern, kinds in SHAPE_RULES:
            if pattern.fullmatch(t):
                return {**base, "kinds": list(kinds), "primary": kinds[0],
                        "source": "shape_rule", "in_db_columns": [],
                        "shape_rule": pattern.pattern,
                        "reason": "not in registry — shape fallback"}
        return {**base, "kinds": [], "primary": "", "source": "unknown",
                "in_db_columns": [], "shape_rule": None,
                "reason": "not in registry and no shape rule matches"}


# Module-level cached instance (one per process) so the index builds once.
_classifier: Optional[IdentifierClassifier] = None


def get_classifier() -> IdentifierClassifier:
    global _classifier
    path = config.INTELLIGENCE_DB
    if _classifier is None or _classifier.db_path != str(path):
        _classifier = IdentifierClassifier(path)
    return _classifier


def classify(token: str) -> List[str]:
    """Classify a single identifier token (see IdentifierClassifier.classify)."""
    return get_classifier().classify(token)


def classify_many(tokens: List[str]) -> Dict[str, List[str]]:
    """Classify many tokens -> dict kind -> [tokens] (deduped, order kept).

    A token that the DB says is e.g. both mxcode and payable lands in BOTH
    lists — resolution later tries every column anyway, so nothing is lost.
    """
    out: Dict[str, List[str]] = {}
    seen = set()
    for tok in tokens:
        t = (tok or "").strip()
        if not t:
            continue
        key = t.upper()
        if key in seen:
            continue
        seen.add(key)
        for kind in classify(t):
            out.setdefault(kind, []).append(t)
    return out
