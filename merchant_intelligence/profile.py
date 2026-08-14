"""
profile.py — Merchant 360° profile aggregation.

Takes ANY fragment of merchant information (a name, an email, a phone
number, an MX code, a TID, an account number, an address…) and returns
EVERYTHING the registry knows about that merchant, aggregated across all
rows that share identifiers with the seed.

The result is a single structure:

  - seed          — the best matching record (with score / match type)
  - identity      — deduplicated unique values per field (all emails,
                    all phones, all TIDs, all MX codes, all addresses…)
                    each annotated with which merchant names carry it
  - name_variants — every distinct merchant_name in the family
                    (same merchant under different spellings / aliases)
  - sources       — every file / sheet the merchant appears in
  - members       — the full family rows, each with its link reasons

Usage:
    from merchant_intelligence.profile import MerchantProfile
    p = MerchantProfile()
    data = p.build("smonsuru@filmhouseng.com")
"""

import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from . import config
from . import settings as engine_settings
from .database import DatabaseManager
from .entity import EntityResolver, same_merchant_family
from .fuzzy import normalize_code, normalize_email, normalize_phone
from .matcher import MerchantMatcher

logger = logging.getLogger(__name__)

# Display order for identity fields (shared by the identity grid and the
# compare() per-field table so the ordering can never drift).
FIELD_PRIORITY = ["email", "phone", "tid", "mxcode", "payable_code",
                  "merchant_id", "account_number", "account_name",
                  "contact_name", "contact_title", "bank", "address",
                  "state", "terminal_serial", "slip_header",
                  "onboarded_date"]

# Fields we aggregate into the identity section. Each entry is
# (db_column, human label, normaliser, icon name for the frontend).
IDENTITY_FIELDS = [
    ("email", "Emails", normalize_email, "mail"),
    ("phone", "Phones", normalize_phone, "call"),
    ("tid", "TIDs", normalize_code, "point_of_sale"),
    ("mxcode", "MX Codes", normalize_code, "credit_card"),
    ("payable_code", "Payable Codes", normalize_code, "tag"),
    ("merchant_id", "Merchant IDs", normalize_code, "badge"),
    ("account_number", "Account Numbers", normalize_code, "account_balance"),
    ("account_name", "Account Names", None, "account_balance_wallet"),
    ("address", "Addresses", None, "location_on"),
    ("contact_name", "Contacts", None, "person"),
    ("contact_title", "Contact Titles", None, "badge"),
    # bank codes resolve to names via config.bank_name (070 → Fidelity Bank),
    # so the UI never shows bare NIBSS codes as if they were bank names.
    ("bank", "Banks", lambda v: config.bank_name(v).upper(), "account_balance"),
    ("state", "States", None, "map"),
    ("terminal_serial", "Terminal Serials", normalize_code, "memory"),
    ("slip_header", "Slip Headers", None, "receipt_long"),
    # When the merchant was onboarded (the MONTH OF REQUEST / DATE CREATED
    # columns). Earliest date first so the profile shows when the merchant
    # first entered the registry.
    ("onboarded_date", "Onboarded", None, "event"),
]

# Values that are noise, not data (EMAIL ALERTS flag etc.)
_NOISE = {"Y", "N", "NA", "N/A", "-", "--", "NIL", "NULL", "0", "1"}


class MerchantProfile:
    """Aggregates everything the registry knows about a merchant fragment."""

    def __init__(self, db_path: Optional[str] = None):
        self.db = DatabaseManager(db_path)
        self.resolver = EntityResolver(db_path)
        self.matcher = MerchantMatcher(self.db)

    # ── Public API ───────────────────────────────────────────────────────

    def build(self, query: str, max_members: int = 200,
              min_score: Optional[float] = None) -> Dict[str, Any]:
        """Build the full 360° profile for a search fragment.

        Args:
            query: any fragment — name, email, phone, MX code, TID, account…
            max_members: cap on the number of family rows returned.
            min_score: minimum seed score. Defaults to POSSIBLE_THRESHOLD
                so garbage queries ("ZZZ NOTHING…" scoring ~10 on code-name
                noise rows) report NOT FOUND instead of a nonsense profile.

        Returns:
            {
              "query", "found", "elapsed_ms",
              "seed": {best SearchResult.to_dict()},
              "identity": {field: {"label", "icon", "values": [{value, count,
                            names, raw_values}]}},
              "name_variants": [{name, count}],
              "sources": [{sheet, count}],
              "family_count": n,
              "members": [ {record…, "link_reasons": [...]} ],
            }
        """
        query = (query or "").strip()
        if not query:
            return {"query": query, "found": False, "identity": {},
                    "name_variants": [], "sources": [], "members": [],
                    "family_count": 0}

        t0 = time.perf_counter()

        if min_score is None:
            min_score = float(config.POSSIBLE_THRESHOLD)

        # 1. Find the seed(s) — any column can match (name, email, phone, …)
        results = self.matcher.search(query, limit=5, min_score=min_score)
        if not results:
            return {
                "query": query, "found": False,
                "identity": {}, "name_variants": [], "sources": [],
                "members": [], "family_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        best = results[0]
        seed_records = [r.record for r in results]

        # Decisive-match guard: when a NAME search (not an identifier search)
        # wins at or above DECISIVE_MATCH_THRESHOLD (the 9.0/10 the user sees),
        # only expand the family from records of the SAME merchant as the
        # winner. Low-scoring lookalike seeds — e.g. "OKI TINA" also surfacing
        # EMOKINIOVO OMOWHO (~54) or OKIEMUTE EKOKIFO (~51) — must not drag
        # their own unrelated families into the relationship network.
        # MEDPLUS is unaffected: all its top rows are MEDPLUS rows, and its
        # many entries share name tokens / TIDs, so they stay together.
        # Identifier matches (phone/email/TID/MX) are exempt — every returned
        # row genuinely shares the queried value, so those families stay.
        if (best.identifier_hit is None
                and best.overall_score >= engine_settings.decisive_match_threshold()
                and len(seed_records) > 1):
            winner = best.record
            seed_records = [rec for rec in seed_records
                            if same_merchant_family(rec, winner)]
            # Never leave the family empty — the winner always seeds it.
            if not seed_records:
                seed_records = [winner]

        # 2. Expand to the whole family via shared identifiers
        family = self.resolver.family_from_records(
            seed_records, merchant_name=query, max_members=max_members)
        members = family.get("members", [])

        # 3. Aggregate identity + variants + sources
        identity = self._aggregate_identity(members)
        name_variants = self._name_variants(members)
        sources = self._sources(members)

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        return {
            "query": query,
            "found": True,
            "elapsed_ms": elapsed_ms,
            "seed": best.to_dict(),
            "identity": identity,
            "name_variants": name_variants,
            "sources": sources,
            "family_count": len(members),
            "alias_candidates": family.get("alias_candidates", []),
            "members": members,
        }

    # ── Comparison ───────────────────────────────────────────────────────

    # Values that show up on MANY unrelated merchants and must never count
    # as a "shared identifier" (e.g. a default TID, a state code, a title).
    _JUNK_SHARED = {"507", "MR", "MRS", "LA", "LAGOS", "NG", "NA", "N/A",
                    "0", "1", "NIL", "DEFAULT"}
    # Fields that actually prove two merchants are the same entity.
    STRONG_FIELDS = {"email", "phone", "mxcode", "merchant_id",
                     "account_number", "payable_code"}

    def compare(self, query_a: str, query_b: str, max_members: int = 200,
                min_score: Optional[float] = None) -> Dict[str, Any]:
        """Build two profiles and diff them field by field.

        Returns both full profiles (``a`` / ``b``) plus:

          - ``shared``       — identifier values present in BOTH profiles
                               (``{field: [values]}``), junk values (default
                               TIDs, state codes, titles…) filtered out
          - ``strong_count`` — how many shared values are on STRONG_FIELDS
                               (email/phone/mxcode/MID/account/payable) — the
                               real "same merchant" signal
          - ``fields``       — per-field comparison rows
                               ``{field, label, icon, a, b, shared, status}``
                               where status is one of
                               match / overlap / only_a / only_b / differ
          - ``overlap_count``— number of family member rows present in both
          - ``seed_names_equal`` / ``name_overlap`` — name-level signals

        Used by the Profile page's side-by-side compare mode.
        """
        a = self.build(query_a, max_members=max_members, min_score=min_score)
        b = self.build(query_b, max_members=max_members, min_score=min_score)

        ia = a.get("identity", {}) or {}
        ib = b.get("identity", {}) or {}

        # 1. shared identifier values across both identity maps,
        #    filtering out values that are junk / generic across merchants
        shared: Dict[str, List[str]] = {}
        for field, fa in ia.items():
            fb = ib.get(field)
            if not fb:
                continue
            va = {v["canonical"] for v in fa["values"]}
            vb = {v["canonical"] for v in fb["values"]}
            inter = sorted(
                v for v in (va & vb)
                if len(v) >= 3 and v.upper() not in self._JUNK_SHARED
            )
            if inter:
                shared[field] = inter
        strong_count = sum(
            len(vals) for f, vals in shared.items()
            if f in self.STRONG_FIELDS)

        # 2. per-field comparison table (identity-field priority order)
        fields: List[Dict[str, Any]] = []
        for field in FIELD_PRIORITY:
            fa, fb = ia.get(field), ib.get(field)
            if not fa and not fb:
                continue
            va = [v["canonical"] for v in (fa["values"] if fa else [])]
            vb = [v["canonical"] for v in (fb["values"] if fb else [])]
            sa, sb = set(va), set(vb)
            shared_clean = sorted(
                v for v in (sa & sb)
                if len(v) >= 3 and v.upper() not in self._JUNK_SHARED
            )
            if sa and sa == sb:
                status = "match"
            elif shared_clean:
                status = "overlap"
            elif va and not vb:
                status = "only_a"
            elif vb and not va:
                status = "only_b"
            elif va and vb:
                status = "differ"
            else:
                continue
            fields.append({
                "field": field,
                "label": (fa or fb)["label"],
                "icon": (fa or fb)["icon"],
                "a": va[:10],
                "b": vb[:10],
                "shared": shared_clean[:10],
                "status": status,
            })

        # 3. family overlap (rows present in both families)
        ids_a = {m.get("id") for m in a.get("members", [])}
        ids_b = {m.get("id") for m in b.get("members", [])}

        # 4. name-level signals (e.g. SPAR's seed IS ARTEE INDUSTRIES LIMITED)
        name_a = str((a.get("seed") or {}).get("merchant_name", "")).strip().upper()
        name_b = str((b.get("seed") or {}).get("merchant_name", "")).strip().upper()
        seed_names_equal = bool(name_a and name_a == name_b)
        variants_a = {v["name"].upper() for v in a.get("name_variants", [])}
        variants_b = {v["name"].upper() for v in b.get("name_variants", [])}
        name_overlap = sorted(variants_a & variants_b)

        return {
            "query_a": query_a,
            "query_b": query_b,
            "a": a,
            "b": b,
            "shared": shared,
            "strong_count": strong_count,
            "fields": fields,
            "overlap_count": len(ids_a & ids_b),
            "seed_names_equal": seed_names_equal,
            "name_overlap": name_overlap[:20],
            "elapsed_ms": round(
                (a.get("elapsed_ms", 0) or 0) + (b.get("elapsed_ms", 0) or 0), 1),
        }

    # ── Aggregation helpers ──────────────────────────────────────────────

    @staticmethod
    def _clean(value: Any) -> str:
        """Trim + upper-case a value for dedup; '' when it's noise."""
        v = str(value or "").strip()
        if not v or v.upper() in _NOISE:
            return ""
        return v

    def _aggregate_identity(self, members: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Group unique values per field across all family members.

        Each value records:
          - value: the canonical (normalised) form
          - raw_values: the distinct raw spellings seen (e.g. phone with and
            without +234) so the UI can show the most readable one
          - count: how many rows carry this identifier
          - names: distinct merchant_names carrying this identifier
        """
        identity: Dict[str, Dict[str, Any]] = {}

        for field, label, normaliser, icon in IDENTITY_FIELDS:
            buckets: Dict[str, Dict[str, Any]] = {}

            for m in members:
                raw = self._clean(m.get(field))
                if not raw:
                    continue
                norm = normaliser(raw) if normaliser else raw.upper()
                if not norm or len(norm) < 2:
                    continue

                bucket = buckets.setdefault(norm, {
                    "value": norm,
                    "raw_values": set(),
                    "count": 0,
                    "names": set(),
                })
                bucket["raw_values"].add(raw)
                bucket["count"] += 1
                name = self._clean(m.get("merchant_name"))
                if name:
                    bucket["names"].add(name)

            if not buckets:
                continue

            values = []
            for b in buckets.values():
                # Keep the raw spelling that best preserves the identifier
                # (e.g. prefer a value containing '@' or the longest form),
                # falling back to the canonical form.
                raws = sorted(b["raw_values"], key=lambda r: (
                    -len(r), "@" not in r))
                display = raws[0] if raws else b["value"]
                # Banks: canonical is the resolved NAME (070 → FIDELITY
                # BANK) which is far more useful than the raw code — prefer
                # it for display while keeping the raw code in raw_values.
                if field == "bank" and b["value"] != display and \
                        any(ch.isalpha() for ch in b["value"]):
                    display = b["value"]
                values.append({
                    "value": display,
                    "canonical": b["value"],
                    "count": b["count"],
                    "names": sorted(b["names"]),
                })
            values.sort(key=lambda v: (-v["count"], v["value"]))

            identity[field] = {
                "label": label,
                "icon": icon,
                "total": len(values),
                "values": values,
            }

        # Order: identifiers/contacts first, descriptive fields last.
        ordered = {}
        for field in FIELD_PRIORITY:
            if field in identity:
                ordered[field] = identity[field]
        return ordered

    @staticmethod
    def _name_variants(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Distinct merchant names across the family, most common first."""
        counter: Counter = Counter()
        for m in members:
            name = str(m.get("merchant_name") or "").strip()
            if name:
                counter[name] += 1
        return [{"name": n, "count": c} for n, c in counter.most_common(40)]

    @staticmethod
    def _sources(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Distinct sheets/files the family appears in, most common first."""
        counter: Counter = Counter()
        for m in members:
            sheet = str(m.get("sheet_name") or "").strip()
            if sheet:
                counter[sheet] += 1
        return [{"sheet": s, "count": c} for s, c in counter.most_common(40)]

    def __repr__(self):
        return f"<MerchantProfile db={self.db.db_path}>"
