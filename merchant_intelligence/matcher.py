"""
matcher.py — Phases 3, 4, 5: Token Intelligence, Row Search, and Field Scoring.

Enhanced with:
  - Fuzzy token matching (catches DENIKE -> ADENIKE without aliases)
  - Levenshtein edit distance (handles typos and spelling variations)
  - Substring prefix matching ("POWER" matches "POWERFOIL")
  - Per-token similarity scoring with configurable thresholds
  - Multi-token boost for merchants matching more query tokens
"""
import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import config
from .aliases import AliasEngine
from .database import DatabaseManager
from .fuzzy import (canonicalize, confusable_key, confusable_variants,
                    damerau_levenshtein_similarity, fuzzy_ratio,
                    is_plausible_tid, is_valid_bvn, is_valid_nuban,
                    levenshtein_similarity, partial_ratio, phonetic_similarity,
                    token_set_ratio, token_sort_ratio,
                    normalize_email, normalize_phone)

logger = logging.getLogger(__name__)

# ── Fuzzy matching thresholds ────────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = 0.60    # SequenceMatcher ratio to consider a fuzzy match
SUBSTRING_BONUS       = 0.15    # Extra boost if one token contains another
PREFIX_BONUS          = 0.10    # Extra boost if one token starts with another

# ── Identifier search ────────────────────────────────────────────────────
# Unique identifiers we can search by directly. A match here is decisive —
# the phone / email / TID / MX code identifies the merchant even when the
# merchant NAME tokens don't match at all.
IDENTIFIER_FIELDS = [
    "mxcode", "payable_code", "merchant_id", "tid", "account_number",
    "phone", "email",
]


# ── Query noise stripping ─────────────────────────────────────────────────
# Users paste natural-language instructions into the search box ("get me all
# the information on medplus"). The noise words (GET, ME, ALL, INFORMATION…)
# would otherwise become search tokens that pollute scoring and bury the real
# merchant name. Stripping is QUERY-SIDE only — stored names are never
# altered — and only engages when the query carries an NL trigger word, so a
# legitimate merchant search like "ALL SEASONS HOTEL" passes through intact.

# Field-request words: when they sit between an article and a preposition
# ("the TID for", "the email of", "the bank on"), they are the OUTPUT the
# user wants, never search keywords. "get me the TID for nnpc apata" must
# search for "nnpc apata" — if "TID" stayed in the token list it would match
# stored TID values (2ISW389C…) and lift unrelated merchants (ADDIDE APATA)
# above the real APATA SS - NNPC record. Positional matching (article +
# field + preposition) is what makes this safe: merchant names that contain
# field words ("FIRST BANK", "ACCESS BANK") are never in that position, so
# a global word-list strip — which would break those searches — is not used.
_FIELD_REQUEST_WORDS = (
    "tid", "tids", "terminal id", "terminal ids",
    "email", "emails", "e[- ]?mail", "e[- ]?mails",
    "phone", "phones", "telephone", "telephones", "mobile", "mobiles",
    "mx ?code", "mx ?codes", "mxcode", "mxcodes", "mx",
    "address", "addresses", "location", "locations",
    "bank", "banks", "account", "accounts", "acct", "accts",
    "payable", "payables", "alias", "aliases",
    "contact", "contacts", "serial", "bvn", "mid",
    "beneficiary", "beneficiaries", "settlement", "static",
    "merchant id", "merchant ids", "state", "states", "code", "codes",
)

# Matches "(the|a|my|our…) FIELD (for|of|on|from|to|about|regarding)" — the
# classic field-request pattern. Group 1 is the field word to remove.
_FIELD_REQUEST_RE = re.compile(
    r"\b(?:the|a|my|your|our|this|that|these|those)\s+("
    + "|".join(_FIELD_REQUEST_WORDS)
    + r")\s+(?:for|of|on|from|to|about|regarding)\b",
    re.IGNORECASE,
)


def strip_query_noise(query: str) -> str:
    """Remove instruction/noise words from a query that reads as an NL request.

    Rules:
      - < 3 tokens: untouched ("GET ALL" is not a request; "ALL SEASONS" stays).
      - no NL trigger phrase present: untouched (no false positives).
      - field words are removed ONLY in field-request position (article +
        field + preposition), never globally, so merchant names containing
        them (FIRST BANK, ACCESS BANK) survive.
      - never empties the query: at least one original token is kept.
    """
    q = (query or "").strip()
    tokens = q.split()
    if len(tokens) < 3:
        return q
    q_low = q.lower()
    if not any(ph in q_low for ph in config.QUERY_NL_TRIGGERS):
        return q
    # Positional field-request strip: "the TID for" -> "" (the whole article
    # + field + preposition is request language, never merchant-name
    # material, so it can go entirely). Removing ONLY the field word would
    # leak "from"/"regarding" — prepositions NOT in QUERY_NOISE_WORDS — as
    # stray search tokens, the exact bug class being fixed here.
    q = _FIELD_REQUEST_RE.sub("", q)
    kept = [t for t in q.split() if t.lower() not in config.QUERY_NOISE_WORDS]
    if not kept:
        return q
    return " ".join(kept)


class SearchResult:
    """A single search result with per-field scores."""

    def __init__(self, merchant_id: int, record: Dict[str, Any]):
        self.merchant_id = merchant_id
        self.record = record
        self.field_scores: Dict[str, float] = {}
        self.overall_score: float = 0.0
        self.match_type: str = "Possible Match"
        self.matched_tokens: List[str] = []
        self.token_similarities: Dict[str, float] = {}
        self.boost_secondary: bool = False  # True when merchant_name is irrelevant
                                            # so slip_header/account_name get more weight
        self.query_boost_fields: Dict[str, float] = {}  # field -> boost_factor when
                                                        # query looks like person/bank name
        self.identifier_hit: Optional[str] = None  # set when the query matched a
                                                   # unique identifier (phone, email,
                                                   # TID, MX code, account number, MID)
        self._key_merchants_cache: Optional[List[str]] = None

    def add_field_score(self, field: str, score: float):
        self.field_scores[field] = score

    def compute_overall(self, multi_token_bonus: float = 0.0):
        """
        Weighted sum of all field scores, normalised to 0-100.

        Two additional boost mechanisms:

        1. boost_secondary — When merchant_name is irrelevant (code name,
           or no query tokens matched), slip_header and account_name
           weights are multiplied by CODE_NAME_BOOST.

        2. query_boost_fields — When the query looks like a person name,
           contact_name weight is multiplied by PERSON_NAME_BOOST.
           When it looks like a bank name, account_name weight is
           multiplied by BANK_NAME_BOOST.

        When both boosts target the same field, the total weight multiplier
        is capped at CODE_NAME_BOOST (the larger of the two).
        """
        # Effective weight per field, boosts applied once and shared by both
        # the signal-weighted pass and the no-signal fallback.
        weights = {}
        for field in config.FIELD_WEIGHTS:
            weight = config.FIELD_WEIGHTS.get(field, 5)
            # Boost 1: code-name secondary-column boost
            if self.boost_secondary and field in ("slip_header", "account_name"):
                weight = int(weight * config.CODE_NAME_BOOST)
            # Boost 2: query-type field boost (person name → contact_name,
            # bank name → account_name) — never stacked with the code-name
            # boost on the same field (use whichever is larger).
            if field in self.query_boost_fields:
                if not (self.boost_secondary and field in ("slip_header", "account_name")):
                    weight = int(weight * self.query_boost_fields[field])
            weights[field] = weight

        # Signal pass (dilution fix): only fields with a REAL score contribute
        # to the weighted average. Every field in FIELD_WEIGHTS previously
        # added its weight to the denominator even when it scored 0 — an
        # empty/unrelated field dragged a perfect merchant_name match down to
        # ~65/100, which is why genuine full-name hits never reached "Exact
        # Match" without an identifier or alias boost.
        strong = {f: s for f, s in self.field_scores.items()
                  if s >= config.SIGNAL_FLOOR}
        name_signal = any(f in strong for f in MerchantMatcher.NAME_BEARING_FIELDS)
        # Dilution applies ONLY when a name-bearing field actually matched, or
        # when the match is identifier-decisive (identifier_hit) or a code-name
        # boost (boost_secondary — merchant_name is a code, slip/account carry
        # the evidence). A junk NAME query that only grazes a non-name field
        # (e.g. tid '12345' inside 'ZZ FAKE CORP 12345') must keep the OLD
        # all-weights denominator — otherwise a lone tid/account substring
        # would score ~90 instead of the ~4 the old engine gave it.
        if strong and (name_signal or self.identifier_hit or self.boost_secondary):
            total_weight = sum(weights.get(f, 5) for f in strong)
            weighted_sum = sum(s * weights.get(f, 5) for f, s in strong.items())
        else:
            # No name-bearing signal (or nothing at all) — fall back to all
            # fields so weak/junk queries still rank the same relative way
            # they used to.
            total_weight = sum(weights.values())
            weighted_sum = sum(self.field_scores.get(f, 0.0) * weights[f]
                               for f in weights)

        if total_weight > 0:
            raw_score = weighted_sum / total_weight
        else:
            raw_score = 0.0

        # Apply multi-token bonus: +10% for each additional token matched
        raw_score *= (1.0 + multi_token_bonus)
        self.overall_score = round(min(raw_score, 100.0), 1)

        # Identifier-match boost: an exact match on a unique identifier
        # (phone, email, TID, MX code, account number, MID) is decisive — the
        # record IS the merchant even if the name tokens scored zero. Lift
        # confident identifier hits well above the name-weighted score so they
        # surface to the top of any search.
        if self.identifier_hit:
            id_score = self.field_scores.get(self.identifier_hit, 0)
            if id_score >= 95:
                self.overall_score = max(self.overall_score, 98.0)
                self.match_type = "Exact Match"
                return
            if id_score >= 85:
                self.overall_score = max(self.overall_score, 90.0)
                self.match_type = "High Confidence"
                return

        # Classify
        if self.overall_score >= config.EXACT_MATCH_THRESHOLD:
            self.match_type = "Exact Match"
        elif self.overall_score >= config.HIGH_CONF_THRESHOLD:
            self.match_type = "High Confidence"
        elif self.overall_score >= config.POSSIBLE_THRESHOLD:
            self.match_type = "Possible Match"
        else:
            self.match_type = "Low Confidence"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.merchant_id,
            "sheet": self.record.get("sheet_name", ""),
            "row": self.record.get("row_number", ""),
            "merchant_name": self.record.get("merchant_name", ""),
            "merchant_id": self.record.get("merchant_id", ""),
            "slip_header": self.record.get("slip_header", ""),
            "mxcode": self.record.get("mxcode", ""),
            "payable_code": self.record.get("payable_code", ""),
            "tid": self.record.get("tid", ""),
            "email": self.record.get("email", ""),
            "phone": self.record.get("phone", ""),
            "address": self.record.get("address", ""),
            "contact_name": self.record.get("contact_name", ""),
            "contact_title": self.record.get("contact_title", ""),
            "account_name": self.record.get("account_name", ""),
            "bank": self.record.get("bank", ""),
            "state": self.record.get("state", ""),
            "terminal_serial": self.record.get("terminal_serial", ""),
            "onboarded_date": self.record.get("onboarded_date", ""),
            "alias": self.record.get("alias", ""),
            # Build-time data-quality signal (enrich.compute_quality): a
            # 0-100 score and the flags that cost points, so the UI can badge
            # weak records and the graph can color leaves by completeness.
            "quality_score": self.record.get("quality_score", 100),
            "quality_flags": self.record.get("quality_flags", "[]"),
            "field_scores": self.field_scores,
            "overall_score": self.overall_score,
            "match_type": self.match_type,
            "matched_tokens": self.matched_tokens,
            "matched_field": self.identifier_hit or "",
            # The actual value that matched (e.g. the phone number, MX code,
            # TID, or email address) — lets the UI show WHAT matched, not
            # just which field it matched on.
            "matched_value": (self.record.get(self.identifier_hit, "")
                              if self.identifier_hit else ""),
            # Key-merchant family roots this record's name belongs to
            # (['MEDPLUS'] for a MEDPLUS PHARMACY row) — the same engine
            # key_merchant_matches() the task interpreter uses, so the Search
            # page badge always agrees with task routing / the Rule Engine.
            "key_merchants": self._key_merchants(),
        }

    def _key_merchants(self) -> List[str]:
        """Key-merchant roots for this record's merchant_name (cached).

        Reuses the task engine's key_merchant_matches so the Search page
        badge shows exactly what the Rule Engine panel shows for the same
        name. The import is lazy (inside the method) so matcher.py never
        imports the tasks package at module load — no import-cycle risk —
        and the result is cached per instance so repeated to_dict() calls
        are free.
        """
        if self._key_merchants_cache is None:
            try:
                from .tasks.parser import key_merchant_matches
                self._key_merchants_cache = list(
                    key_merchant_matches(self.record.get("merchant_name", "")
                                         or ""))
            except Exception as exc:  # noqa: BLE001 — badge is a nicety, never break search
                logger.warning("key_merchant_matches failed for %r: %s",
                               self.record.get("merchant_name", ""), exc)
                self._key_merchants_cache = []
        return self._key_merchants_cache

    def __repr__(self):
        return (f"<SearchResult id={self.merchant_id} "
                f"name={self.record.get('merchant_name','')[:30]} "
                f"score={self.overall_score}>")


class MerchantMatcher:
    """
    Token-based matcher with fuzzy matching, per-field weighted scoring,
    and multi-token boost.
    """

    # Fields where an alias target may legitimately appear. merchant_name is
    # the common case, but person/bank aliases often live in account_name or
    # contact_name (e.g. MUSSAN OIL NIGERIA LIMITED -> "KOLA AMUSAN" is the
    # account_name on the WHITEVILL HOTEL rows).
    TARGET_FIELDS = ("merchant_name", "slip_header", "account_name", "contact_name")
    # Fields whose match counts as a "name" signal for the dilution pass — a
    # row only gets the signal-weighted score when one of these matched.
    NAME_BEARING_FIELDS = ("merchant_name", "slip_header", "account_name",
                           "contact_name")

    @staticmethod
    def _row_has_target(row, target: str) -> bool:
        """True when the alias target appears verbatim in a name-bearing field."""
        t = target.upper()
        return any(t in str(row.get(f) or "").upper()
                   for f in MerchantMatcher.TARGET_FIELDS)

    def __init__(self, db: DatabaseManager, use_aliases: bool = True):
        self.db = db
        self.use_aliases = use_aliases
        self.alias_engine = AliasEngine()
        # Token -> DB count cache for compound-expansion viability checks.
        # Each probe is a LIKE query (~0.5s on this registry), so caching
        # turns a handful of DB round-trips per query into zero.
        self._token_stats: Dict[str, int] = {}

    # ── Main Search ───────────────────────────────────────────────────────

    def search(self, query: str,
               limit: int = 50,
               min_score: float = 0) -> List[SearchResult]:
        """
        Full search pipeline with improved fuzzy matching.

        1. FTS5 full-text search (filtered through tokenise to remove stop words)
        2. Per-token column search (fallback)
        3. Fuzzy token matching (new!)
        4. Per-field weighted scoring with multi-token boost
        """
        query = query.strip()
        if not query:
            return []

        # NL hygiene: "get me all the information on medplus" -> "medplus"
        # so instruction words can't pollute the token scores. The raw query
        # is still passed to identifier search — phone/email/code matching
        # needs the untouched value.
        raw_query = query
        query = strip_query_noise(query)

        tokens = self._tokenise(query)

        # 0. Compound token expansion — split long tokens into known sub-tokens
        #    (e.g. POWERFOIL → POWER + FOIL, MONEYTRUST → MONEY + TRUST)
        expanded_tokens = self._expand_compound_tokens(tokens)

        # 0b. Instant normalized bucket lookup — canonicalized merchant names
        #     (generics stripped) map straight to row ids, so a query whose
        #     canonical form matches a stored name resolves in one indexed
        #     read before any fuzzy work. Skips cleanly when the DB has no
        #     bucket table yet (ensure_buckets builds it lazily on first use).
        bucket_rows: List[Dict[str, Any]] = []
        try:
            self.db.ensure_buckets()
            # Reuse the same canonical key logic as the bucket table builder
            # (DatabaseManager._bucket_key) so query and stored keys agree.
            bucket_rows = self.db.lookup_bucket(
                DatabaseManager._bucket_key(query))
        except Exception:
            bucket_rows = []  # buckets are an optimisation — never break search

        # 1. FTS5 search — use tokenised tokens to filter out stop words.
        #    SORTED so the MATCH query string is identical across processes:
        #    Python set iteration order depends on the per-process hash seed,
        #    which would otherwise change FTS5 rank (and therefore the
        #    candidate window) run-to-run — the source of the old
        #    benchmark flakiness.
        fts_tokens = sorted(set(tokens + expanded_tokens))  # deduplicated
        fts_query = " ".join(fts_tokens) if fts_tokens else query
        fts_results = self.db.search_fts(fts_query, limit=limit * 3) if fts_query else []

        # 2. Column search (using expanded tokens for broader retrieval)
        col_results = self._column_search(fts_tokens, limit=limit * 2)

        # Merge and deduplicate
        seen_ids: Set[int] = set()
        all_results: List[SearchResult] = []

        # Bucket hits first — exact normalized matches are the most decisive.
        for row in bucket_rows:
            rid = row["id"]
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            result = self._score_row(row, tokens, query)
            if result.overall_score >= min_score:
                all_results.append(result)

        # 0c. Fuzzy bucket-key scan — near-exact canonical names (one typo,
        #     token order, dropped branch word) recovered in a single pass over
        #     the distinct-name index (far smaller than the 76k-row table)
        #     before any FTS work. Skips cleanly when rapidfuzz is missing.
        if self.db.has_buckets() and 0 < len(tokens) <= 4:
            try:
                keys = self.db.bucket_keys()
                if keys:
                    from rapidfuzz import fuzz as _rf_fuzz
                    from rapidfuzz import process as _rf_process
                    for key, _scr, _idx in _rf_process.extract(
                            query, keys, scorer=_rf_fuzz.token_set_ratio,
                            limit=limit, score_cutoff=70):
                        # Probe window matches ALIAS_PROBE_LIMIT so
                        # high-cardinality buckets (ATREOS: 63 rows) don't
                        # truncate the rows carrying the real email.
                        for row in self.db.lookup_bucket(
                                key, limit=config.ALIAS_PROBE_LIMIT):
                            rid = row["id"]
                            if rid in seen_ids:
                                continue
                            seen_ids.add(rid)
                            result = self._score_row(row, tokens, query)
                            if result.overall_score >= min_score:
                                all_results.append(result)
            except Exception:
                pass  # fuzzy buckets are an optimisation — never break search

        for row in fts_results:
            rid = row["id"]
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            result = self._score_row(row, tokens, query)
            if result.overall_score >= min_score:
                all_results.append(result)

        for row in col_results:
            rid = row["id"]
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            result = self._score_row(row, tokens, query)
            if result.overall_score >= min_score:
                all_results.append(result)

        # 2b. Trigram FTS — substring-tolerant retrieval for compound words
        #     and partial names the word-level index cannot see. Runs after
        #     the primary sources so deduplication state is already set up.
        if self.db.has_trigram_index():
            trigram_results = self.db.search_fts_trigram(query, limit=limit * 3)
            for row in trigram_results:
                rid = row["id"]
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                result = self._score_row(row, tokens, query)
                if result.overall_score >= min_score:
                    all_results.append(result)

        # 3. Alias expansion — if the query matches a known manual alias,
        #    search directly for the DB record names and apply a score boost.
        #    Skipped in alias-free mode (used by the self-improving harness
        #    to measure the RAW engine strength without hand-added mappings).
        alias_results = self._alias_search(query, tokens, limit) \
            if self.use_aliases else []

        # Merge alias results into all_results. Alias results are DECISIVE
        # (they come from a confirmed manual alias), so when the regular
        # pipeline already scored the same row weakly — e.g. a fuzzy name
        # match that found FOLASHADE KALEJAIYE before the alias probe ran —
        # the alias version REPLACES the weak entry instead of being skipped.
        # The old append-only merge silently dropped these rows, leaving the
        # correct records (HARRISON's merchant17@example.com row, FOLASHADE's
        # merchant14@example.com rows) buried at Low-Confidence scores outside
        # the result window.
        alias_by_id = {r.merchant_id: r for r in alias_results}
        for idx, r in enumerate(all_results):
            if r.merchant_id in alias_by_id:
                better = alias_by_id[r.merchant_id]
                # Replace on strictly-greater, OR on a tie when the alias
                # version is the confirmed one (a 100.0 name-exact normal
                # row must not win an equal-score tie by insertion order).
                # Never replace with a LOWER alias score (the pre-70-floor
                # 97.7 < 100.0 case).
                if better.overall_score > r.overall_score or (
                        better.overall_score == r.overall_score
                        and better.match_type == "Alias Match"
                        and r.match_type != "Alias Match"):
                    all_results[idx] = better
                alias_by_id.pop(r.merchant_id)
        for r in alias_by_id.values():
            all_results.append(r)

        # 4. Identifier search — phone / email / TID / MX code / account /
        #    MID. An exact identifier hit identifies the merchant outright,
        #    so it OVERRIDES the (low) score the fuzzy pipeline gave the same
        #    row rather than being merged alongside it. Only override when the
        #    identifier version scores higher, so a strong name match (e.g. 95)
        #    is never downgraded by a weaker identifier substring hit (90).
        id_results = self._identifier_search(raw_query, limit)
        id_by_id = {r.merchant_id: r for r in id_results}
        for idx, r in enumerate(all_results):
            if r.merchant_id in id_by_id:
                better = id_by_id[r.merchant_id]
                if better.overall_score > r.overall_score:
                    all_results[idx] = better
                id_by_id.pop(r.merchant_id)
        for r in id_by_id.values():
            all_results.append(r)

        # Sort by score descending. Ties are broken by email presence so the
        # actionable record (carrying a real contact address) leads equal-score
        # groups — this survives the in-place alias merge, which otherwise
        # preserves the bare sibling's earlier insertion position. Non-ties are
        # unaffected (stable sort, primary key dominates).
        # 5. Family expansion — when a search finds a strong match, also
        # Sort and cap the main results first, THEN run family expansion
        # on the capped set.  This ensures the family expansion only skips
        # TIDs the user actually sees (not TIDs from fuzzy matches that
        # were trimmed by the limit).
        all_results.sort(
            key=lambda r: (
                r.overall_score,
                "@" in str(r.record.get("email") or ""),
            ),
            reverse=True,
        )
        main_capped = all_results[:limit]

        # 5. Family expansion — when a search finds a strong match, also
        #    surface other terminals sharing the same MX code or contact
        #    email.  This catches families like MEGALEK (56 hospital
        #    terminals under one MX174102) that would otherwise require
        #    knowing the MX code to discover.  Family results are ALWAYS
        #    appended in full — they are not capped by the search limit.
        family_results = self._family_expansion(main_capped)
        if family_results:
            main_capped.extend(family_results)
        return main_capped

    # ── Family expansion ────────────────────────────────────────────────

    def _family_expansion(self,
                          results: List[SearchResult]) -> List[SearchResult]:
        """Find sibling terminals sharing MX codes or contact emails.

        After the main search finds e.g. 'MEGALEK LIMITED', this probes
        the DB for all rows with the same mxcode (MX174102) or contact
        email, adding them as 'Family Match' results so the user sees
        the full family in one search.
        """
        # Collect linkage keys ONLY from high-confidence results.
        # The main pipeline may return many results for 'MEGALEK' (fuzzy
        # hits on ADELEKE, REMILEKUN, etc.), but only the top-scored
        # ones are actually part of the target family.  First find the
        # dominant MX code from the best results, then collect emails
        # ONLY from results sharing that MX code (or having no MX code
        # but matching the family contact).
        mx_codes: Set[str] = set()
        contact_emails: Set[str] = set()
        # Dedup by TID — but ONLY from results sharing the dominant
        # MX code.  TIDs from unrelated fuzzy matches (ADELEKE etc.)
        # must NOT block family expansion — those TIDs were never
        # returned to the user.
        seen_tids: Set[str] = set()

        # Step 1: find the dominant MX code from top results
        dominant_mx = None
        mx_counts: Dict[str, int] = {}
        for r in results:
            if r.overall_score < 80:
                break
            mx = (r.record.get("mxcode") or "").strip().upper()
            if mx and mx.startswith("MX"):
                mx_counts[mx] = mx_counts.get(mx, 0) + 1
        if mx_counts:
            dominant_mx = max(mx_counts, key=mx_counts.get)
            mx_codes.add(dominant_mx)

        # Step 2: collect emails only from results sharing the dominant MX
        # (or having no MX but being in the top 5 — likely the HQ entry)
        for r in results:
            if r.overall_score < 80:
                break
            rec = r.record
            mx = (rec.get("mxcode") or "").strip().upper()
            email = (rec.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            if dominant_mx and mx == dominant_mx:
                contact_emails.add(email)
            elif not mx and r.overall_score >= 85:
                # HQ entry without MX code (e.g. MEGALEK LIMITED 2ISW266C)
                contact_emails.add(email)

        # Step 3: mark TIDs already returned by the main search ONLY
        # when they belong to the same family (dominant MX or same email).
        for r in results:
            tid = (r.record.get("tid") or "").strip().upper()
            if not tid:
                continue
            mx = (r.record.get("mxcode") or "").strip().upper()
            em = (r.record.get("email") or "").strip().lower()
            if (dominant_mx and mx == dominant_mx) or em in contact_emails:
                seen_tids.add(tid)

        if not mx_codes and not contact_emails:
            return []

        # Query DB for siblings
        db = self.db
        siblings: List[Dict[str, Any]] = []
        try:
            with db._lock:
                conn = db._get_connection()
                c = conn.cursor()
                conditions = []
                params: list = []
                for mx in mx_codes:
                    conditions.append("UPPER(mxcode) = ?")
                    params.append(mx)
                for em in contact_emails:
                    conditions.append("LOWER(email) = ?")
                    params.append(em)
                if not conditions:
                    return []
                query = (
                    f"SELECT * FROM merchants WHERE "
                    f"{' OR '.join(conditions)}"
                )
                c.execute(query, params)
                siblings = [dict(row) for row in c.fetchall()]
        except Exception as exc:
            logger.debug("Family expansion query failed: %s", exc)
            return []

        # Build results for siblings not already in the main set.
        # Dedup by TID: if the main search already returned a row
        # for TID X (even from a different sheet), skip all other
        # rows for that TID.
        family: List[SearchResult] = []
        for row in siblings:
            tid = (row.get("tid") or "").strip().upper()
            if tid in seen_tids:
                continue
            seen_tids.add(tid)
            result = SearchResult(row.get("id"), row)
            result.overall_score = 85.0  # family matches are high-confidence
            result.match_type = "Family Match"
            # Tag with the linkage key that found them
            mx = (row.get("mxcode") or "").strip().upper()
            email = (row.get("email") or "").strip().lower()
            if mx and mx in mx_codes:
                result.matched_tokens = [f"family:{mx}"]
            elif email and email in contact_emails:
                result.matched_tokens = [f"family:{email}"]
            else:
                result.matched_tokens = ["family:linkage"]
            family.append(result)

        return family

    # ── Alias-backed search (Phase 2 integration) ─────────────────────────

    def _alias_search(self, query: str, tokens: List[str],
                       limit: int = 50) -> List[SearchResult]:
        """
        Look up the query in the alias engine and search for known DB records.

        When the user searches for "MARYLAND MALL LIMITED REVENUE COLLECTION ACCOUNT",
        and we have a manual alias mapping to "SWEB_MARYLAND MALL", this method
        finds that DB record and scores it highly using the alias field.
        """
        results: List[SearchResult] = []

        canonical = self.alias_engine.lookup(query)
        if not canonical:
            return results

        # Get the target DB record names for this alias
        alias_targets = self.alias_engine.manual_aliases.get(canonical, [])
        if not alias_targets:
            return results

        # Deduplicate near-identical targets ("Filmhouse" vs "Film House") by
        # their first token and cap the probe count — each DB probe costs time.
        seen_probes: Set[str] = set()
        targets = []
        for target in alias_targets:
            if not target or len(target) < config.MIN_TOKEN_LENGTH:
                continue
            key = str(target).strip().upper().split()
            key = key[0] if key else str(target).upper()
            if key in seen_probes:
                continue
            seen_probes.add(key)
            targets.append(target)
            if len(targets) >= 4:
                break

        seen_ids: Set[int] = set()

        for target in targets:
            # Retrieve via the fast trigram index when available (substring-
            # tolerant, ~30x faster than a LIKE scan), verifying the target
            # still appears in a name-bearing field so we don't widen
            # semantics. The target need NOT be in merchant_name: person and
            # bank aliases commonly live in account_name / contact_name
            # (e.g. MUSSAN OIL NIGERIA LIMITED's "KOLA AMUSAN" is the
            # account_name on the WHITEVILL HOTEL rows). Probe limit is
            # generous (20) because short targets like "G & G ENTERPRISE"
            # reduce to a single generic trigram token ("ENTERPRISE") that
            # matches hundreds of merchants — the right row must not be
            # crowded out of a tiny limit=3 window.
            # Probe window is generous (config.ALIAS_PROBE_LIMIT) because
            # high-cardinality merchants can have 60-210 rows with the same
            # name — e.g. ATREOS (63 rows) and ARTEE (210 rows). A tight
            # window truncates the rows carrying the REAL email (e.g. ATREOS
            # rows 41749+ with merchant5@example.com ranked 26-27, missing the
            # alias boost entirely), leaving only bare email='Y' siblings
            # boosted.
            rows: List[Dict[str, Any]] = []
            if self.db.has_trigram_index():
                rows = [row for row in
                        self.db.search_fts_trigram(target, limit=config.ALIAS_PROBE_LIMIT)
                        if self._row_has_target(row, target)]
            if not rows:
                # Fallback: exact-substring LIKE probe. Catches alias targets
                # whose trigram form is too generic to surface (e.g. the
                # single token "ENTERPRISE" crowding out "G & G ENTERPRISES"
                # from the trigram window).
                rows = self.db.search_by_column(
                    "merchant_name", target, limit=config.ALIAS_PROBE_LIMIT)
                rows = [row for row in rows if self._row_has_target(row, target)]
            for row in rows:
                rid = row["id"]
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)

                # Score this row with alias boost.
                # Instead of calling _score_row (which calls compute_overall
                # internally), we build the result directly and override
                # the key field scores since we KNOW this is the right merchant.
                record = dict(row)
                result = SearchResult(record.get("id"), record)

                # Apply field scores from normal scoring, but only keep the
                # ones with real signal. The alias confirms the identity, so
                # a populated-but-unrelated secondary field (e.g. the
                # account_name "KOLA AMUSAN" on WHITEVILL HOTEL rows when
                # searching "MUSSAN OIL NIGERIA LIMITED") must not drag the
                # weighted average below a bare row whose fields are empty.
                # Weak fields are dropped from field_scores (the deep-analysis
                # panel only shows meaningful matches), so they no longer
                # dilute the alias-confirmed score.
                for field in config.FIELD_WEIGHTS:
                    field_value = record.get(field)
                    if not field_value:
                        continue
                    score = self._score_field(str(field_value), tokens, query, None)
                    if score >= config.ALIAS_MIN_FIELD_SCORE:
                        result.add_field_score(field, score)

                # The alias target may sit in a non-merchant field — person
                # and bank aliases commonly live in account_name / contact_name
                # (e.g. MUSSAN OIL NIGERIA LIMITED's "KOLA AMUSAN" is the
                # account_name on the WHITEVILL HOTEL rows). Confirm whichever
                # field carries it so the CORRECT row outranks bare same-name
                # rows from other sheets (which never carry the target).
                for field in MerchantMatcher.TARGET_FIELDS:
                    if target.upper() in str(record.get(field) or "").upper():
                        result.field_scores[field] = 100.0
                        break

                result.field_scores["merchant_name"] = 100.0
                result.field_scores["alias"] = 100.0

                # Compute overall with high alias-backed scores
                result.compute_overall(0.0)
                result.match_type = "Alias Match"
                results.append(result)

        # Deterministic ordering for the UI: among equal alias-confirmed
        # scores, prefer rows that carry a real email address — they are the
        # richer records (e.g. HARRISON EZEASOMBA appears on both a bare
        # row and one carrying merchant17@example.com; the email row should
        # lead so the user sees the actionable record first).
        results.sort(
            key=lambda r: (
                r.overall_score,
                "@" in str(r.record.get("email") or ""),
            ),
            reverse=True,
        )
        return results

    # ── Scoring (Phase 5 — improved) ──────────────────────────────────────

    def _score_row(self, row, tokens: List[str],
                   raw_query: str) -> SearchResult:
        """Score a single database row with multi-token boost."""
        record = dict(row)
        result = SearchResult(record.get("id"), record)

        # Tokenise the merchant name (for fuzzy matching)
        merchant_name = str(record.get("merchant_name", "") or "")
        merchant_tokens = self._tokenise(merchant_name)

        # Track how many query tokens matched (fuzzily or exactly)
        matched_tokens = []
        token_similarities = {}

        for qtoken in tokens:
            best_sim = self._best_token_similarity(qtoken, merchant_tokens)
            token_similarities[qtoken] = best_sim
            if best_sim >= FUZZY_MATCH_THRESHOLD:
                matched_tokens.append(qtoken)

        result.matched_tokens = matched_tokens
        result.token_similarities = token_similarities

        # Token-rarity (IDF) weighting — rarer tokens carry more evidence than
        # common ones. A token shared by hundreds of merchants (BANK, GLOBAL)
        # must count for less than a distinctive one (MONEYTRUST). name_cov =
        # the rarity-weighted fraction of the query the merchant NAME matched;
        # when all tokens are equally rare it reduces to plain coverage
        # (matched / total), i.e. the pre-IDF behaviour.
        idf = {t: self._idf(t) for t in tokens}
        matched_weight = sum(idf.get(t, 1.0) for t in matched_tokens)
        total_query_weight = sum(idf.values()) or 1.0
        name_cov = (matched_weight / total_query_weight) if total_query_weight else 0.0

        # Calculate multi-token bonus: +10% per rarity-weighted extra match,
        # capped at +30%. The cap is deliberately tight: the dilution fix
        # already lifts genuine full matches to 95-100, so a large bonus only
        # saturates PARTIAL matches to 100 and erases score separation
        # ("ESORAE HOME IKOYI" tying the true "ESORAE IKOYI" alias row).
        if len(tokens) > 1 and matched_tokens:
            extra = max(0.0, name_cov * len(tokens) - 1.0)
            multi_token_bonus = min(extra * 0.10, 0.30)  # cap at +30%
        else:
            multi_token_bonus = 0.0

        # Score each field
        for field in config.FIELD_WEIGHTS:
            field_value = record.get(field)
            if not field_value:
                result.add_field_score(field, 0)
                continue

            ts = token_similarities if field == "merchant_name" else None
            score = self._score_field(str(field_value), tokens, raw_query, ts)
            result.add_field_score(field, score)

        # Coverage penalty: when a multi-token query matches only a fraction
        # of its tokens, a merchant sharing one common word (FIELD, PARK,
        # OCEAN, GLOBAL…) must not score as though the whole query matched.
        # "CRANE FIELD SCHOOL JEDDO" vs "FIELD AND OCEAN" only matches FIELD,
        # so its merchant_name score is scaled down. Only applies to 3+ token
        # queries — short 1-2 token searches are never penalised. Runs AFTER
        # the field-scoring loop so the scaled value is the one used.
        if len(tokens) >= 3 and name_cov < 0.5:
            factor = 0.4 + 0.6 * name_cov   # name_cov 0.25 -> x0.55, 0.49 -> x0.69
            name_score = result.field_scores.get("merchant_name", 0.0)
            result.field_scores["merchant_name"] = name_score * factor

        # Detect if merchant_name is irrelevant to this query:
        #   - It's a numeric code (e.g. "4789.0"), OR
        #   - No query tokens matched the merchant name at all
        # In either case, boost slip_header and account_name so they
        # can lift the overall score.
        no_tokens_matched = len(matched_tokens) == 0 and len(tokens) > 0
        result.boost_secondary = (
            MerchantMatcher._is_code_name(merchant_name)
            or no_tokens_matched
        )

        # Only boost if secondary columns actually have meaningful matches
        if result.boost_secondary:
            slip_score = result.field_scores.get("slip_header", 0)
            acct_score = result.field_scores.get("account_name", 0)
            if slip_score < 30 and acct_score < 30:
                result.boost_secondary = False

        # Detect query type and set field-specific boosts:
        #   - Person-name query → boost contact_name
        #   - Bank-name query  → boost account_name
        if MerchantMatcher._is_person_name_query(tokens):
            result.query_boost_fields["contact_name"] = config.PERSON_NAME_BOOST
        if MerchantMatcher._is_bank_name_query(tokens):
            result.query_boost_fields["account_name"] = config.BANK_NAME_BOOST

        result.compute_overall(multi_token_bonus)
        return result

    def _best_token_similarity(self, query_token: str,
                                merchant_tokens: List[str]) -> float:
        """
        Find the best similarity between a query token and any merchant token.

        Uses:
          - Exact match: 1.0
          - SequenceMatcher fuzzy ratio: 0.0-1.0
          - Substring/prefix bonus
        """
        qt = query_token.upper()
        if not merchant_tokens:
            # Still check if query token appears in the full merchant name
            return 0.0

        best = 0.0
        for mt in merchant_tokens:
            mt_upper = mt.upper()

            # Exact match
            if qt == mt_upper:
                return 1.0

            # Fuzzy ratio (rapidfuzz-backed)
            ratio = fuzzy_ratio(qt, mt_upper)
            if ratio > best:
                best = ratio

            # Token-set ratio: tolerant of extra/subset tokens — "LAGOON
            # WATERS" vs "LAGOON WATER ENT" shares its core set.
            tsr = token_set_ratio(qt, mt_upper)
            if tsr > best:
                best = tsr

            # Substring bonus: one contains the other
            if qt in mt_upper or mt_upper in qt:
                ratio_with_bonus = ratio + SUBSTRING_BONUS
                if ratio_with_bonus > best:
                    best = min(ratio_with_bonus, 1.0)

            # Prefix bonus: one starts with the other
            if mt_upper.startswith(qt) or qt.startswith(mt_upper):
                ratio_with_bonus = ratio + PREFIX_BONUS
                if ratio_with_bonus > best:
                    best = min(ratio_with_bonus, 1.0)

            # Levenshtein + Damerau-Levenshtein for short tokens — Damerau
            # counts a transposition (INTERNMATIONAL → INTERNATIONAL) as ONE
            # edit instead of two, which is the exact typo class in this
            # workbook.
            if len(qt) <= 8 and len(mt_upper) <= 8:
                lev_sim = levenshtein_similarity(qt, mt_upper)
                dam_sim = damerau_levenshtein_similarity(qt, mt_upper)
                best = max(best, lev_sim, dam_sim)

            # Phonetic similarity (Metaphone) — catches transliteration drift
            # (PHILIP ≈ FELIP, KELIZZ ≈ KELIS) without over-scoring.
            # Capped at 0.92 so phonetic evidence alone never yields 1.0.
            ph_sim = phonetic_similarity(qt, mt_upper)
            if ph_sim >= 0.85 and ph_sim > best:
                best = min(ph_sim, 0.92)

        return best

    def _score_field(self, field_value: str, tokens: List[str],
                     raw_query: str,
                     token_similarities: Optional[Dict[str, float]] = None) -> float:
        """
        Score a single field value (0-100) with multiple strategies:

          - Exact match: 100
          - Query in field: 90
          - Field in query: 80
          - Token overlap (exact): 60-100 based on coverage
          - Token overlap (fuzzy): 40-80 based on fuzzy similarity
          - SequenceMatcher on full strings: up to 60
          - Levenshtein on full strings: up to 40
        """
        fv = field_value.upper().strip()
        rq = raw_query.upper().strip()

        # 1. Exact match
        if fv == rq:
            return 100.0

        # 2. Substring: field contains query, or query contains field
        if rq in fv:
            return 90.0
        if fv in rq:
            return 80.0

        # 3. Token-based scoring with fuzzy fallback
        field_tokens = self._tokenise(field_value)
        if not field_tokens or not tokens:
            return 0.0

        query_tokens = [t.upper() for t in tokens]

        # Exact token overlap
        query_set = set(query_tokens)
        field_set = set(t.upper() for t in field_tokens)
        exact_overlap = query_set & field_set
        exact_match_ratio = len(exact_overlap) / len(query_set)

        # Fuzzy token overlap (if exact is low or zero)
        fuzzy_match_count = 0
        for qt in query_tokens:
            for ft in field_tokens:
                ft_upper = ft.upper()
                if qt == ft_upper:
                    continue  # already counted as exact
                sim = fuzzy_ratio(qt, ft_upper)
                if sim >= FUZZY_MATCH_THRESHOLD:
                    fuzzy_match_count += 1
                    break

        fuzzy_match_ratio = fuzzy_match_count / len(query_set) if query_set else 0

        # Use token_similarities if provided (from _score_row)
        if token_similarities:
            avg_sim = sum(token_similarities.values()) / len(token_similarities)
        else:
            # Compute average similarity across all query tokens
            total_sim = 0.0
            for qt in query_tokens:
                best_sim = max(
                    (fuzzy_ratio(qt, ft.upper()) for ft in field_tokens),
                    default=0.0
                )
                total_sim += best_sim
            avg_sim = total_sim / len(query_tokens)

        # 4. Full-string similarity — positional and order-insensitive
        #    (token_sort), plus subset-tolerant (token_set). partial_ratio is
        #    deliberately LIMITED to short-query-vs-long-field cases
        #    ("MEDPLUS" vs "MEDPLUS PHARMACY SANGOTEDO"): applied to long
        #    queries it over-scores prefix matches ("E'SORAE HOME STORES…"
        #    vs "ESORAE HOME IKOYI") which the coverage penalty can't tame.
        full_ratio = token_sort_ratio(rq[:80], fv[:80])
        full_ratio = max(full_ratio, token_set_ratio(rq[:80], fv[:80]))
        if len(rq) >= 4 and len(rq) <= int(len(fv) * 0.5):
            full_ratio = max(full_ratio, partial_ratio(rq[:80], fv[:80]))

        # 5. Levenshtein + Damerau for short strings (transposition-aware)
        if len(rq) <= 15 and len(fv) <= 15:
            lev_sim = max(levenshtein_similarity(rq, fv),
                          damerau_levenshtein_similarity(rq, fv))
        else:
            lev_sim = full_ratio

        # ── Combined score ───────────────────────────────────────────────
        if exact_match_ratio >= 0.5:
            # Mostly exact match: weight exact heavily
            score = (exact_match_ratio * 70) + (fuzzy_match_ratio * 15) + (avg_sim * 15)
        elif fuzzy_match_ratio >= 0.3:
            # Mostly fuzzy match
            score = (fuzzy_match_ratio * 50) + (avg_sim * 30) + (full_ratio * 20)
        else:
            # Mostly full-string + Levenshtein
            score = (full_ratio * 50) + (lev_sim * 30) + (avg_sim * 20)

        # score is already on a 0-100 scale (weights sum to 100, inputs are 0-1).
        # DO NOT multiply by 100 again — that would make even weak matches hit 100.
        return round(min(score, 100.0), 1)

    # ── Column Search ─────────────────────────────────────────────────────

    def _column_search(self, tokens: List[str],
                       limit: int = 50) -> List:
        """Fallback search across multiple columns.

        Fast path: the trigram FTS index covers every searchable column and is
        substring-tolerant, so one query per token replaces the dozens of
        full-table LIKE scans the legacy loop performed (~0.5s each on this
        registry — that made name searches take ~16s). Falls back to the LIKE
        sweep only when no trigram index exists.
        """
        if not tokens:
            return []

        search_cols = [
            "merchant_name", "slip_header", "email", "phone", "address",
            "contact_name", "account_name", "alias", "mxcode",
            "payable_code", "tid", "terminal_serial", "remarks",
            "account_number", "merchant_id",
        ]

        seen_ids: Set[int] = set()
        results = []

        if self.db.has_trigram_index():
            for token in tokens[:5]:
                if len(token) < config.MIN_TOKEN_LENGTH:
                    continue
                for row in self.db.search_fts_trigram(token, limit=limit):
                    if row["id"] not in seen_ids:
                        seen_ids.add(row["id"])
                        results.append(row)
                if len(results) >= limit:
                    break
            return results[:limit]

        # Legacy LIKE sweep (no trigram index available)
        for token in tokens[:5]:
            if len(token) < config.MIN_TOKEN_LENGTH:
                continue
            for col in search_cols:
                try:
                    rows = self.db.search_by_column(col, token, limit=limit)
                    for row in rows:
                        if row["id"] not in seen_ids:
                            seen_ids.add(row["id"])
                            results.append(row)
                except ValueError:
                    continue

        return results[:limit]

    # ── Identifier Search ─────────────────────────────────────────────────
    # Search by whatever the user has: a phone number, an email address, a
    # TID, an MX code, a payable code, an account number or a MID. The raw
    # query is normalised (digits-only for phones, compact for codes,
    # lower-case for emails) and matched directly against each identifier
    # column — this bypasses tokenisation, which would otherwise destroy
    # values like "08000000000" or "merchant20@example.com".
    #
    # Retrieval leans on the trigram FTS index (which covers every identifier
    # column and is substring-tolerant) instead of per-column LIKE scans —
    # the LIKE sweeps cost ~0.5s each on this registry, trigram is ~30ms.

    @staticmethod
    def _compact_code(value: str) -> str:
        """Normalise a code-like value: uppercase, strip non-alphanumerics."""
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    @staticmethod
    def _plausible_identifier(field: str, value: str) -> bool:
        """Format sanity for a code-like identifier value (substring-gate).

        Exact string-identical matches are NEVER gated; this only decides
        whether a SUBSTRING hit (score 90) is plausible for its field, so
        stored junk ('507' as a TID, non-NUBAN account numbers) can't match
        merely because it contains the query digits.
        """
        v = (value or "").strip()
        if not v:
            return False
        if field in ("account_number", "static_acc_no"):
            return is_valid_nuban(v)
        if field == "bvn":
            return is_valid_bvn(v)
        if field == "tid":
            return is_plausible_tid(v)
        if field == "merchant_id":
            return len(v) >= 6
        if field in ("mxcode", "payable_code"):
            return len(v) >= 6
        return True

    @staticmethod
    def _phone_equivalent(a: str, b: str) -> bool:
        """True if two phone digit-strings are the same number.

        Tolerates the Nigerian +234 / 0 prefix forms and leading zeros:
        "08000000000" ≡ "234800000000" ≡ "+234800000000".
        """
        a = (a or "").lstrip("0")
        b = (b or "").lstrip("0")
        if a == b:
            return True
        if a.startswith("234") and a[3:] == b:
            return True
        if b.startswith("234") and b[3:] == a:
            return True
        # Last-10-digits match only when the length difference is exactly a
        # country-code (3) or zero — prevents 08000000000 vs 18098726020
        # (different first digit) from being treated as equivalent.
        return (len(a) >= 10 and len(b) >= 10
                and abs(len(a) - len(b)) in (0, 3)
                and a[-10:] == b[-10:])

    @classmethod
    def _phone_retrieval_forms(cls, q_digits: str) -> Set[str]:
        """Canonical searchable forms for a phone query.

        The trigram FTS tokenizer breaks "234800000000" into trigrams that
        do NOT appear in stored "08000000000", so a +234-formatted query can
        never retrieve the row even though _phone_equivalent would accept it.
        Return the digit form plus the 0-prefixed / 234-prefixed variants so
        retrieval probes can find the stored form.
        """
        forms = {q_digits}
        if q_digits.startswith("234") and len(q_digits) >= 13:
            forms.add("0" + q_digits[3:])
        elif q_digits.startswith("0") and len(q_digits) >= 11:
            forms.add("234" + q_digits[1:])
        return forms
    # Substring identifier matches need a query long enough to be meaningful.
    # A 3-char query like "080" or "123" would otherwise substring-match
    # dozens of account numbers / TIDs and flood results with spurious
    # 90-scoring rows.
    ID_SUBSTRING_MIN = 5

    # Codes that START with letters (MX codes, TID/MID prefixes, 2ISW…) help
    # distinguish code-like queries from plain names.
    CODE_PREFIXES = ("MX", "TID", "MID", "2ISW", "PTSP", "BVN", "PAY", "ACC")

    @staticmethod
    def _looks_code_like(q_compact: str) -> bool:
        """True when a query reads as a code/identifier rather than a name.

        Codes contain digits or start with a code prefix; person and business
        names don't. Gating code-field substring matching on this prevents a
        name search (e.g. "AKANBI", "VICTOR") from false-positiving against a
        stored merchant_id / TID / account_number that merely contains the
        name as a substring.
        """
        if not q_compact or len(q_compact) < config.MIN_TOKEN_LENGTH:
            return False
        if q_compact.startswith(MerchantMatcher.CODE_PREFIXES):
            return True
        # Otherwise require a digit AND enough length to be unambiguous — a
        # 3-char "080" (digit but tiny) would flood substring matching.
        return len(q_compact) >= MerchantMatcher.ID_SUBSTRING_MIN and any(
            ch.isdigit() for ch in q_compact)

    def _identifier_match(self, row: Dict[str, Any], q_compact: str,
                          q_digits: str, q_email: str) -> Tuple[Optional[str], float]:
        """Return (field, score) if this row's identifier matches the query."""
        code_fields = ("mxcode", "payable_code", "merchant_id", "tid",
                       "account_number")

        # Pass 1 — exact matches across ALL code fields first, so an exact hit
        # on a later field isn't masked by a substring hit on an earlier one.
        if len(q_compact) >= config.MIN_TOKEN_LENGTH:
            for field in code_fields:
                nval = self._compact_code(row.get(field) or "")
                if nval and nval == q_compact:
                    return field, 100.0

        # Pass 1b — confusable-equivalent matches (0↔O, 1↔I, 2↔Z, 5↔S, 8↔B).
        # TID '2103O265' (letter O) is routinely typed as '21030265' (digit 0).
        # The DB is the ground truth: a confusable spelling only counts when
        # the registry actually stores it, so nothing is ever invented.
        if len(q_compact) >= config.MIN_TOKEN_LENGTH:
            for field in code_fields:
                nval = self._compact_code(row.get(field) or "")
                if nval and confusable_key(nval) == confusable_key(q_compact):
                    return field, 98.0

        # Pass 2 — substring matches only for code-like queries of meaningful
        # length (short queries would flood; plain names would false-positive).
        if self._looks_code_like(q_compact):
            for field in code_fields:
                nval = self._compact_code(row.get(field) or "")
                if nval and (q_compact in nval or nval in q_compact):
                    # Substring credit only for plausible formats — junk
                    # values ('507' TID, non-NUBAN accounts) must not match
                    # merely because they contain the query digits.
                    if (self._plausible_identifier(field, q_compact)
                            or self._plausible_identifier(field, nval)):
                        return field, 90.0

        # Phone
        if q_digits:
            nval = normalize_phone(row.get("phone") or "")
            if nval:
                if self._phone_equivalent(nval, q_digits):
                    return "phone", 100.0
                if len(q_digits) >= self.ID_SUBSTRING_MIN \
                        and (q_digits in nval or nval in q_digits):
                    return "phone", 90.0
        # Email
        if q_email and "@" in q_email:
            nval = normalize_email(row.get("email") or "")
            if nval:
                if nval == q_email:
                    return "email", 100.0
                if len(q_email) >= self.ID_SUBSTRING_MIN \
                        and (q_email in nval or nval in q_email):
                    return "email", 90.0
        return None, 0.0

    def _identifier_search(self, query: str,
                           limit: int = 50) -> List[SearchResult]:
        """
        Search for records whose unique identifier matches the query.

        Returns SearchResults with .identifier_hit set, so compute_overall
        can lift them to Exact / High-Confidence.
        """
        q = (query or "").strip()
        if not q:
            return []

        q_compact = self._compact_code(q)
        q_digits = normalize_phone(q)   # '' if not a phone-like value
        q_email = normalize_email(q)

        # Bail out early for queries that can't plausibly be identifiers — a
        # pure name like "LAGOON" or a tiny fragment like "080" otherwise
        # floods trigram retrieval with thousands of candidates that never
        # identifier-match. Partial phones (>= 7 digits) are still allowed.
        is_phone = len(q_digits) >= 7
        is_email = bool(q_email) and "@" in q_email
        is_code = self._looks_code_like(q_compact)
        if not (is_phone or is_email or is_code):
            return []

        # Candidate rows: fast trigram FTS when available; fall back to
        # targeted LIKE probes on the identifier columns otherwise. For phone
        # queries we probe EVERY canonical form (+234 / 0 prefix) because the
        # trigram tokenizer can't span the prefix difference.
        candidates: List[Dict[str, Any]] = []
        if self.db.has_trigram_index():
            probes = [q]
            if q_digits:
                probes.extend(sorted(self._phone_retrieval_forms(q_digits)))
            # Confusable spellings (0↔O, 1↔I, …) widen retrieval so a TID
            # typed as digits finds the row stored with the look-alike letter
            # ('21030265' retrieves the '2103O265' row). Skipped for
            # phone-shaped queries — phones are stored digits-only, so a
            # letter variant can never exist and would only waste probes.
            if not q_digits and len(q_compact) >= config.MIN_TOKEN_LENGTH:
                probes.extend(confusable_variants(q_compact))
            # q may already be one of the phone forms (pure-digit query) —
            # deduplicate so we don't run the same trigram query twice.
            probes = list(dict.fromkeys(probes))
            for probe in probes:
                candidates.extend(self.db.search_fts_trigram(probe, limit=limit * 3))
        if not candidates:
            for field in IDENTIFIER_FIELDS:
                if field in ("mxcode", "payable_code", "merchant_id", "tid",
                             "account_number"):
                    if len(q_compact) < config.MIN_TOKEN_LENGTH:
                        continue
                    probes = [q_compact]
                elif field == "phone":
                    if len(q_digits) < 7:
                        continue
                    probes = sorted(self._phone_retrieval_forms(q_digits))
                else:
                    if "@" not in q_email:
                        continue
                    probes = [q_email]
                for probe in probes:
                    try:
                        candidates.extend(self.db.search_by_column(field, probe,
                                                                   limit=limit))
                    except ValueError:
                        continue

        results: List[SearchResult] = []
        seen_ids: Set[int] = set()
        query_tokens = self._tokenise(query)

        for row in candidates:
            rid = row["id"]
            if rid in seen_ids:
                continue
            hit_field, match_score = self._identifier_match(
                row, q_compact, q_digits, q_email)
            if hit_field is None:
                continue
            seen_ids.add(rid)
            record = dict(row)
            result = SearchResult(record.get("id"), record)

            # Score the other fields so the deep-analysis panel stays meaningful.
            merchant_ts = None
            m_tokens = self._tokenise(str(record.get("merchant_name", "") or ""))
            if m_tokens:
                merchant_ts = {qt: self._best_token_similarity(qt, m_tokens)
                               for qt in query_tokens}
            for f in config.FIELD_WEIGHTS:
                fv = record.get(f)
                if not fv:
                    result.add_field_score(f, 0)
                    continue
                sims = merchant_ts if f == "merchant_name" else None
                result.add_field_score(
                    f, self._score_field(str(fv), query_tokens, query, sims))

            # Override the matched identifier field and mark the hit
            result.field_scores[hit_field] = match_score
            result.identifier_hit = hit_field
            result.compute_overall(0.0)
            results.append(result)
            if len(results) >= limit:
                break

        return results

    # ── Compound Token Expansion ──────────────────────────────────────────

    def _expand_compound_tokens(self, tokens: List[str]) -> List[str]:
        """
        Expand long tokens by splitting at known compound boundaries.

        For example, "POWERFOIL" → splits into "POWER" + "FOIL" (both known)
        "MONEYTRUST" → splits into "MONEY" + "TRUST" (MONEY known, TRUST in DB)

        Only accepts splits where BOTH parts are viable (known word OR exist in DB)
        to avoid meaningless splits like "POWERF" + "OIL".
        """
        extra: List[str] = []

        for token in tokens:
            upper = token.upper()
            if len(upper) <= config.MIN_TOKEN_LENGTH + 3:
                continue  # too short to be compound

            best_prefix = None
            best_suffix = None
            best_score = -1

            # Try every possible split point (prefix >= 3, suffix >= 3)
            for split_at in range(config.MIN_TOKEN_LENGTH,
                                   len(upper) - config.MIN_TOKEN_LENGTH + 1):
                prefix = upper[:split_at]
                suffix = upper[split_at:]

                # Both parts must be >= MIN_TOKEN_LENGTH
                if len(prefix) < config.MIN_TOKEN_LENGTH or len(suffix) < config.MIN_TOKEN_LENGTH:
                    continue

                # Check viability: known word or exists in DB
                prefix_viable = (prefix in config.KNOWN_PREFIXES or
                                 self._token_exists_in_db(prefix))
                suffix_viable = (suffix in config.KNOWN_SUFFIXES or
                                 self._token_exists_in_db(suffix))

                if not prefix_viable or not suffix_viable:
                    continue  # both parts must be viable

                # Score: prefer known words, prefer higher DB counts
                # Known words get a high base score (50) to outweigh accidental
                # DB substring matches (e.g. "LTH" matching HEALTH via LIKE).
                p_score = 50 if prefix in config.KNOWN_PREFIXES else self._token_db_count(prefix)
                s_score = 50 if suffix in config.KNOWN_SUFFIXES else self._token_db_count(suffix)
                score = p_score + s_score

                # Bonus if both are known words (pairs like POWER+FOIL)
                if prefix in config.KNOWN_PREFIXES and suffix in config.KNOWN_SUFFIXES:
                    score += 50

                if score > best_score:
                    best_prefix = prefix
                    best_suffix = suffix
                    best_score = score

            if best_prefix and best_suffix:
                if best_prefix not in extra and best_prefix not in tokens:
                    extra.append(best_prefix)
                if best_suffix not in extra and best_suffix not in tokens:
                    extra.append(best_suffix)

        return extra

    def _token_exists_in_db(self, token: str) -> bool:
        """Check if a token exists in any merchant_name in the DB (cached)."""
        return self._token_db_count(token) > 0

    def _token_db_count(self, token: str) -> int:
        """Get row count of merchants containing this token (cached).

        Uses the trigram FTS COUNT fast path when available (real counts, not
        the old limit-20 LIKE approximation) — better rarity estimates for IDF
        weighting AND better compound-split viability checks."""
        if not token or len(token) < config.MIN_TOKEN_LENGTH:
            return 0
        if token in self._token_stats:
            return self._token_stats[token]
        count = 0
        try:
            count = self.db.count_tokens("merchant_name", token)
        except ValueError:
            count = 0
        self._token_stats[token] = count
        return count

    def _total_rows(self) -> int:
        """Total registry rows (cached) — denominator for IDF weights."""
        try:
            return self.db.count_rows()
        except Exception:
            return 0

    def _idf(self, token: str) -> float:
        """Inverse document frequency: log(1 + N/count). Rarer token -> higher."""
        count = self._token_db_count(token)
        total = self._total_rows() or 1
        return math.log1p(total / max(count, 1))

    # ── Token Intelligence ────────────────────────────────────────────────

    @staticmethod
    def _tokenise(text: str) -> List[str]:
        """
        Break text into meaningful search tokens.

        Strips generic words (THE, LTD, LIMITED, NIGERIA, etc.) and short tokens.
        Preserves significant words for matching.
        """
        if not text:
            return []
        # Normalize first (canonicalize) so G&G → G AND G, INT'L →
        # INTERNATIONAL, E'SORAE → ESORAE — identically for query and stored
        # names, which is what makes the normalized bucket table work.
        text = canonicalize(text)
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        words = text.split()

        generic = set(w.upper() for w in config.GENERIC_WORDS)
        significant = [
            w for w in words
            if w not in generic and len(w) >= config.MIN_TOKEN_LENGTH
        ]

        if not significant:
            return [w for w in words if len(w) >= config.MIN_TOKEN_LENGTH]

        return significant

    # ── Code-name detection ───────────────────────────────────────────────
    # A "code name" is a numeric string like "4789.0", "5411.0", "6012.0"
    # that is clearly not a real merchant name. When merchant_name is a
    # code, the search engine boosts slip_header and account_name scores.

    @staticmethod
    def _is_code_name(name: str) -> bool:
        """Check if a merchant_name is a numeric code, not a real name."""
        if not name:
            return False
        name = name.strip()
        if len(name) <= 2:
            return True  # too short to be a real name
        # Pure numbers/dots (e.g. "4789.0", "5411.0", "507.0")
        if re.match(r'^[\d.]+$', name):
            return True
        # Short codes with mixed chars but no real words (e.g. "2ISW1234")
        if re.match(r'^[\dA-Z]+$', name) and len(name) <= 10:
            # If it starts with a digit, it's likely a code
            if name[0].isdigit():
                return True
            # If it's all uppercase with no vowels, it's likely a code
            vowel_count = sum(1 for c in name if c in 'AEIOU')
            if vowel_count == 0 and len(name) >= 5:
                return True
        return False

    # ── Query-type detection ──────────────────────────────────────────────
    # These methods classify the user's search query to apply field-specific
    # weight boosts. Person-name queries get contact_name boosted; bank-name
    # queries get account_name boosted.

    @staticmethod
    def _is_person_name_query(tokens: List[str]) -> bool:
        """Check if query tokens look like a person name.

        Heuristics:
        - Query has 2-3 tokens (first + last name, sometimes middle)
        - At least one token matches a known person-name marker
        - No token looks like a business entity (LTD, COMPANY, etc.)
        """
        if not tokens or len(tokens) > 4:
            return False

        token_upper = [t.upper() for t in tokens]
        markers = config.PERSON_NAME_MARKERS

        # Check for known name markers
        marker_hits = sum(1 for t in token_upper if t in markers)
        if marker_hits >= 1:
            return True

        return False

    @staticmethod
    def _is_bank_name_query(tokens: List[str]) -> bool:
        """Check if query tokens indicate a bank/financial institution search."""
        if not tokens:
            return False

        # Quick check: does any token match a bank keyword?
        token_upper = [t.upper() for t in tokens]
        for keyword in config.BANK_KEYWORDS:
            kw_upper = keyword.upper()
            for t in token_upper:
                if kw_upper in t or t in kw_upper:
                    return True

        return False

    # ── Levenshtein Distance ──────────────────────────────────────────────

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """Compute Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return MerchantMatcher._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                # Calculate insertions, deletions, substitutions
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    @staticmethod
    def _levenshtein_similarity(s1: str, s2: str) -> float:
        """
        Convert Levenshtein distance to a 0.0-1.0 similarity score.
        """
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0

        distance = MerchantMatcher._levenshtein_distance(
            s1.upper(), s2.upper()
        )
        return 1.0 - (distance / max_len)

    def __repr__(self):
        return f"<MerchantMatcher db={self.db.db_path}>"
