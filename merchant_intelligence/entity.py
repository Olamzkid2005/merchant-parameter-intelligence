"""
entity.py — Entity resolution and merchant relationship graph.

Takes the search engine to the next level:

  - Link records across sheets/files by shared identifiers
    (email, phone, MX code, TID, account number, payable code, merchant_id).
  - Discover merchant "families": records that are connected through
    shared contact details even when the names look completely different
    (e.g. MONEYTRUST MICROFINANCE <-> CASCADES LUXURY's settlement account).
  - Suggest alias candidates from family members so confirmed relationships
    can be auto-learned (Phase 10) and persisted to merchant_aliases.json.

Usage:
    from merchant_intelligence.entity import EntityResolver
    er = EntityResolver()
    family = er.family_of("LAGOON WATERS")
    for member in family["members"]: ...
"""

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from . import config
from .database import DatabaseManager
from .fuzzy import normalize_code, normalize_email, normalize_phone

logger = logging.getLogger(__name__)

# Fields we use to connect records into a family. Each entry maps the DB
# column name to a normaliser used for exact-linking.
LINK_FIELDS = [
    ("email", normalize_email),
    ("phone", normalize_phone),
    ("mxcode", normalize_code),
    ("tid", normalize_code),
    ("payable_code", normalize_code),
    ("account_number", normalize_code),
    ("merchant_id", normalize_code),
]

# Primary identifiers used for graph traversal (hops beyond the seed).
# Email/phone are too generic — they connect unrelated merchants through
# shared contact domains or common phone patterns.
GRAPH_LINK_FIELDS = [
    ("mxcode", normalize_code),
    ("tid", normalize_code),
    ("payable_code", normalize_code),
    ("account_number", normalize_code),
    ("merchant_id", normalize_code),
]

# ── Identifier plausibility ────────────────────────────────────────────────
# Dirty columns in the workbook leak non-identifiers into the identifier
# columns (e.g. `tid=507` is really TERMINAL OWNER CODE, `tid=POS` is
# TERMINAL TYPE, `bank` holds NIBSS codes). Linking on those garbage values
# fans the graph out to every record sharing them (38,884 rows share 507!).
# These DB-derived shapes say whether a value LOOKS like a real identifier
# for its field — derived from the actual distributions in intelligence.db:
#   tid           8-char (2ISW166C) or 8-digit (21030173) or 2103O338
#   mxcode        MX\d{4,8}
#   merchant_id   2ISW + 11 chars (15 total)
#   payable_code  7-digit or Default_Payable_MX...
#   account_no    10-digit
#   phone         Nigerian mobile / +234 form
#   email         must contain @
_PLAUSIBLE_SHAPES = {
    "tid":            re.compile(r"^(?:2ISW[A-Z0-9]{4,6}|\d{8}|\d{4}[A-Z]\d{3})$", re.I),
    "mxcode":         re.compile(r"^MX\d{4,8}$", re.I),
    "merchant_id":    re.compile(r"^2ISW[A-Z0-9]{11}$", re.I),
    "payable_code":   re.compile(r"^(?:\d{7}|Default[_-]?Payable[_-]?MX\d+|MX\d+_[A-Z_]+)$", re.I),
    "account_number": re.compile(r"^\d{10}$"),
    "phone":          re.compile(r"^(?:\+?234|0)[789]\d{9}$"),
}


def _plausible(field: str, value: str) -> bool:
    """True when `value` looks like a real identifier for `field`.

    Email uses its own rule (contains @ + a dot in the domain). Values that
    fail the shape (507, POS, GPRS, 1, 2…) are treated as non-identifiers and
    never create family/graph links.
    """
    v = (value or "").strip()
    if not v or len(v) < 4:
        return False
    if field == "email":
        return "@" in v and "." in v.split("@")[-1]
    pattern = _PLAUSIBLE_SHAPES.get(field)
    if pattern is None:
        return True  # unknown field — don't block
    return bool(pattern.match(v.upper()))


# Words that don't identify a merchant (stripped before core-name comparison).
_GENERIC_WORDS = {
    "LTD", "LIMITED", "PLC", "INC", "LLC", "NIG", "NIGERIA", "NG", "CO",
    "COMPANY", "COMPANIES", "SERVICES", "SERVICE", "ENTERPRISE",
    "ENTERPRISES", "VENTURES", "VENTURE", "GROUP", "GLOBAL", "INTL",
    "INTERNATIONAL", "INVESTMENT", "INVESTMENTS", "HOLDING", "HOLDINGS",
    "AND", "&", "THE", "OF", "FOR", "STORE", "STORES", "BRANCH",
    "NNPC", "LTD.",
}


def _core_tokens(name: str) -> Set[str]:
    """Distinctive tokens of a merchant name (generics stripped).

    'LAGOON WATERS LTD - NNPC' → {'LAGOON', 'WATERS'}.
    'FILMHOUSE CINEMA - IKOTA' → {'FILMHOUSE', 'CINEMA', 'IKOTA'}.
    """
    s = (name or "").upper().replace("&", " AND ")
    toks = {t for t in re.split(r"[^A-Z0-9]+", s) if len(t) >= 2}
    return toks - _GENERIC_WORDS


def _common_core(tokens_list) -> Set[str]:
    """Tokens shared by EVERY name in the list — the merchant's core identity.

    Returns empty when the names share no distinctive token (i.e. they are
    different merchants connected only by an owner-level identifier like a
    shared contact email or an owner's MX code).
    """
    if not tokens_list:
        return set()
    inter = set(tokens_list[0])
    for t in tokens_list[1:]:
        inter &= t
    return inter


# Primary identifiers that prove two records are the SAME merchant even when
# their names look nothing alike (MEDPLUS LIMITED <-> MEDPLUS PHARMACY rows
# share TIDs/MX codes; a profile seed and a variant row share the account).
# Email/phone are deliberately excluded — a shared contact detail spans
# unrelated merchants and must not count as "same merchant" evidence.
_SAME_MERCHANT_ID_FIELDS = ("tid", "mxcode", "merchant_id",
                            "payable_code", "account_number")


def same_merchant_family(rec_a: Dict[str, Any], rec_b: Dict[str, Any]) -> bool:
    """True when two records plausibly belong to the SAME merchant.

    Used to guard family expansion: when a name search wins decisively, only
    records sharing a distinctive name token OR a primary identifier with the
    winner are kept as seeds, so unrelated lookalike hits (e.g. "OKI TINA"
    surfacing AGATHA IKPOTOKIN / OKIEMUTE EKOKIFO) can't drag their own
    families into the relationship network — while MEDPLUS's many rows (same
    name tokens / same TID) still aggregate.

    Evidence is DB-rooted:
      1. Core-token overlap in merchant_name (generics stripped), e.g.
         "TINA VENTURE" vs "Tina Oki" share {TINA}. This branch is
         deliberately more permissive than the identifier branch: it only
         sees top-5 seeds that share the query's distinctive token with the
         winner, so genuinely different merchants sharing a common name
         word (e.g. OKIOKPA STELLA for a bare "OKI" query) are still
         dropped — their core tokens don't overlap the winner's.
      2. A shared normalised primary identifier value (TID/MX/MID/payable/
         account) — same terminal / same settlement account.
    """
    na = _core_tokens(rec_a.get("merchant_name", "") or "")
    nb = _core_tokens(rec_b.get("merchant_name", "") or "")
    if na and nb and (na & nb):
        return True
    for field in _SAME_MERCHANT_ID_FIELDS:
        va = str(rec_a.get(field) or "").strip().upper()
        vb = str(rec_b.get(field) or "").strip().upper()
        if va and vb and va == vb:
            return True
    return False


def _link_values(rec: Dict[str, Any], fields) -> Dict[str, Set[str]]:
    """Collect the plausible, normalised link values of a record."""
    out: Dict[str, Set[str]] = defaultdict(set)
    for field, normaliser in fields:
        val = normaliser(rec.get(field, "") or "")
        if val and _plausible(field, val):
            out[field].add(val)
    return out


class EntityResolver:
    """Resolves merchant families and relationship graphs from the DB."""

    def __init__(self, db_path: Optional[str] = None):
        self.db = DatabaseManager(db_path)
        self._conn = self.db.connect()

    # ── Core: family discovery ───────────────────────────────────────────

    def family_of(self, merchant_name: str,
                  min_members: int = 1,
                  max_members: int = 200) -> Dict[str, Any]:
        """Find all records linked to a merchant by shared identifiers.

        Returns:
            {
              "seed": merchant_name,
              "members": [ {record..., "link_reasons": [...]}, ... ],
              "shared": {field: {value: [member_names]}},
              "alias_candidates": [name, ...],
            }
        """
        seeds = self._find_seeds(merchant_name)
        if not seeds:
            return {"seed": merchant_name, "members": [], "shared": {}, "alias_candidates": []}
        return self.family_from_records(seeds, merchant_name=merchant_name,
                                        max_members=max_members)

    def family_from_records(self, seed_records: List[Dict[str, Any]],
                            merchant_name: str = "",
                            max_members: int = 200) -> Dict[str, Any]:
        """Build a merchant family from explicit seed records.

        Unlike ``family_of`` (which seeds by name lookup), this accepts any
        records — e.g. the top hits of an identifier search (a phone number,
        email or MX code fragment). Every record sharing a normalised
        identifier with any seed becomes a member, so the profile page can
        show ALL data attached to a fragment no matter which column matched.

        Returns the same shape as ``family_of``.
        """
        if not seed_records:
            return {"seed": merchant_name, "members": [], "shared": {}, "alias_candidates": []}

        # Collect link values from the seed records
        link_values: Dict[str, Set[str]] = defaultdict(set)   # field -> {value}
        for rec in seed_records:
            for field, values in _link_values(rec, LINK_FIELDS).items():
                link_values[field].update(values)

        # Expand: find every record sharing any of those values
        members: List[Dict[str, Any]] = []
        seen_ids: Set[int] = set()
        shared_usage: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        alias_candidates: Dict[str, int] = defaultdict(int)

        for field, values in link_values.items():
            for value in values:
                # Only link through a value whose records are all the same
                # merchant family (see _core_specific) — owner-level values
                # (a contact email / an owner MX) span many merchants.
                if not self._core_specific(field, value):
                    continue
                rows = self._find_by_field(field, value, limit=max_members)
                for row in rows:
                    rid = row.get("id")
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    reason = f"{field}={value}"
                    member = dict(row)
                    member["link_reasons"] = [reason]
                    members.append(member)
                    shared_usage[field][value].append(str(row.get("merchant_name", "")))

        # Alias candidates: other member names that connect via >=1 shared
        # identifier and look different from the seed.
        seed_names = {str(r.get("merchant_name", "")).strip().upper() for r in seed_records}
        for member in members:
            name = str(member.get("merchant_name", "")).strip().upper()
            if not name or name in seed_names:
                continue
            alias_candidates[name] += len(member.get("link_reasons", []))

        ranked_candidates = sorted(alias_candidates, key=alias_candidates.get, reverse=True)

        return {
            "seed": merchant_name,
            "members": members[:max_members],
            "shared": {f: dict(v) for f, v in shared_usage.items()},
            "alias_candidates": ranked_candidates,
        }

    # ── Relationship graph (BFS through shared identifiers) ──────────────

    def graph(self, merchant_name: str, depth: int = 2,
              max_nodes: int = 50) -> Dict[str, Any]:
        """BFS traversal of the merchant relationship graph.

        Nodes are records; edges exist when two records share a normalised
        identifier value. `depth=1` returns the direct family; `depth=2`
        also shows what those members connect to.

        The seed (level 0) uses all LINK_FIELDS so we find all of a
        merchant's own records (across sheets/files). Hops beyond the seed
        use only GRAPH_LINK_FIELDS (TID, MX, MID, payable, account) —
        email/phone are too generic and cause false fan-out between
        unrelated merchants.
        """
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_ids: Set[int] = set()

        frontier = self._find_seeds(merchant_name)
        for rec in frontier:
            if rec.get("id") not in seen_ids:
                seen_ids.add(rec["id"])
                node = self._node(rec)
                node["depth"] = 0  # the seed record(s)
                nodes.append(node)

        for level in range(depth):
            next_frontier: List[Dict[str, Any]] = []
            # Level 0→1: use all LINK_FIELDS (find the merchant's own records
            # across files). Level 1→2+: restrict to primary identifiers to
            # avoid false fan-out through shared emails/phones.
            hop_fields = LINK_FIELDS if level == 0 else GRAPH_LINK_FIELDS
            for rec in frontier:
                for field, values in _link_values(rec, hop_fields).items():
                    for val in values:
                        # Same-merchant-only guard (see _core_specific).
                        if not self._core_specific(field, val):
                            continue
                        others = self._find_by_field(field, val, limit=30)
                        for other in others:
                            oid = other.get("id")
                            if oid in seen_ids:
                                continue
                            seen_ids.add(oid)
                            next_frontier.append(other)
                            node = self._node(other)
                            node["depth"] = level + 1
                            nodes.append(node)
                            edges.append({
                                "source": rec.get("id"),
                                "target": oid,
                                "field": field,
                                "value": val,
                            })
                            if len(nodes) >= max_nodes:
                                return {"seed": merchant_name, "nodes": nodes, "edges": edges}
            frontier = next_frontier

        return {"seed": merchant_name, "nodes": nodes, "edges": edges}

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_seeds(self, merchant_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Find seed records by exact or LIKE match on merchant_name."""
        name = (merchant_name or "").strip()
        if not name:
            return []
        rows = self.db.search_by_column("merchant_name", name, limit=limit)
        if rows:
            return rows
        # Fallback: search the slip header (some merchants only appear there)
        rows = self.db.search_by_column("slip_header", name, limit=limit)
        if rows:
            return rows
        # Fallback: search the account name (e.g. MONEYTRUST appears only as
        # CASCADES LUXURY's settlement account)
        rows = self.db.search_by_column("account_name", name, limit=limit)
        return rows

    def _find_by_field(self, field: str, value: str,
                       limit: int = 50) -> List[Dict[str, Any]]:
        """Exact-match lookup on a linking field."""
        try:
            return self.db.search_by_column(field, value, limit=limit)
        except ValueError:
            logger.warning("Field %r not searchable — skipped", field)
            return []

    def _core_specific(self, field: str, value: str) -> bool:
        """True when the records sharing a value are the SAME merchant family.

        Owner-level identifiers (a contact's email, an owner's MX code) get
        attached to many DIFFERENT merchants. We only create links through a
        value when every distinct merchant name carrying it belongs to one
        merchant family, proven either by:

          1. a common distinctive name token (JUST CHIPS's rows across
             sheets), OR
          2. a shared FULL identifier signature — identical tid+mxcode+
             merchant_id+account_number tuples under every name. This is how
             two files name the same terminal rows differently: the NNPC
             batch file calls them "LAGOON WATERS LTD" while the NNpc
             parameter master calls the SAME tids/MXs/MIDs/accounts
             "Interswitch Limited/NNPC 15/16".

        Values that only share an owner-level detail (a contact phone, an
        owner MX code) have no shared signature and stay blocked — JUST CHIPS
        and OLAWALE ODUOLA share MX154553/email/phone but have DIFFERENT
        MIDs and accounts, so they never merge.

        Queries the DB directly (not a sampled row list) so the verdict is
        stable regardless of the row limit.
        """
        try:
            names = [r[0] for r in self._conn.execute(
                f"SELECT DISTINCT merchant_name FROM merchants "
                f"WHERE {field} = ? AND TRIM(COALESCE(merchant_name, '')) != ''",
                (value,))]
        except sqlite3.Error:
            return True  # don't block on query failure
        core_sets = [_core_tokens(n) for n in names]
        if len(core_sets) <= 1:
            return True
        if _common_core(core_sets):
            return True
        # Different display names — fall back to the full-signature test.
        # One extra query for the same rows (name + identifiers) is bounded:
        # it only runs for values whose names share no token, not per row.
        return self._names_share_signature(field, value)

    def _names_share_signature(self, field: str, value: str) -> bool:
        """True when every distinct name carrying a value shares one merchant
        identity through identical primary-identifier tuples.

        Each name's rows (for this value) contribute their
        (tid, mxcode, merchant_id, account_number) signatures; two names are
        connected when they share at least one signature. The value is
        merchant-specific only when ALL names form one connected component —
        so a phone shared by three genuinely different merchants (no common
        signature) still blocks, while LAGOON WATERS + Interswitch NNPC rows
        (identical tuples) pass.

        A signature needs >= 2 non-empty identity fields: a row with only
        tid+mx non-empty still counts (identical tid+mx under two names is
        strong same-terminal evidence), but all-blank rows prove nothing and
        are skipped.
        """
        try:
            rows = self._conn.execute(
                f"SELECT merchant_name, tid, mxcode, merchant_id, account_number "
                f"FROM merchants WHERE {field} = ? "
                f"AND TRIM(COALESCE(merchant_name, '')) != ''",
                (value,)).fetchall()
        except sqlite3.Error:
            return True  # don't block on query failure

        # name -> set of full signatures (skip rows with too little identity
        # content — e.g. an OLAWALE row whose MID/account are blank can't
        # prove sameness with JUST CHIPS).
        all_names: Set[str] = set()
        sigs: Dict[str, Set[Tuple[str, str, str, str]]] = defaultdict(set)
        for name, tid, mx, mid, acct in rows:
            name_key = str(name).strip().upper()
            all_names.add(name_key)
            sig = (str(tid or "").strip().upper(),
                   str(mx or "").strip().upper(),
                   str(mid or "").strip().upper(),
                   str(acct or "").strip().upper())
            if sum(1 for s in sig if s) < 2:
                continue  # blank-ish row: no identity evidence
            sigs[name_key].add(sig)

        # Every distinct name carrying the value must contribute at least
        # one signature — if any name has ONLY blank-ish rows, we can't prove
        # it is the same merchant, so the value stays blocked (conservative:
        # linking it would pull that possibly-unrelated merchant's rows in).
        name_list = [n for n in all_names if n in sigs]
        if len(name_list) < len(all_names):
            return False
        if len(name_list) <= 1:
            return True

        # Adjacency by shared signature, then a single connected-component
        # sweep (names are few per value, so O(n^2) is fine).
        adj: Dict[str, Set[str]] = {n: set() for n in name_list}
        for i, a in enumerate(name_list):
            for b in name_list[i + 1:]:
                if sigs[a] & sigs[b]:
                    adj[a].add(b)
                    adj[b].add(a)
        seen: Set[str] = {name_list[0]}
        stack = [name_list[0]]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return len(seen) == len(name_list)

    @staticmethod
    def _node(rec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": rec.get("id"),
            "name": rec.get("merchant_name", "") or "",
            "sheet": rec.get("sheet_name", ""),
            "email": rec.get("email", "") or "",
            "phone": rec.get("phone", "") or "",
            "mxcode": rec.get("mxcode", "") or "",
            "tid": rec.get("tid", "") or "",
            "payable_code": rec.get("payable_code", "") or "",
            "merchant_id": rec.get("merchant_id", "") or "",
            "account_name": rec.get("account_name", "") or "",
            "bank": rec.get("bank", "") or "",
        }

    def __repr__(self):
        return f"<EntityResolver db={self.db.db_path}>"
