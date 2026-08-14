"""
search.py — High-level MerchantSearch class.

Provides a simplified interface for:
  - Full merchant search with scoring (delegates to MerchantMatcher)
  - Per-token breakdown search for NOT FOUND analysis
"""
import logging
from typing import Any, Dict, List, Optional

from . import config
from .database import DatabaseManager
from .fuzzy import fuzzy_ratio, phonetic_similarity
from .matcher import MerchantMatcher, strip_query_noise

logger = logging.getLogger(__name__)


class MerchantSearch:
    """High-level merchant search interface.

    Wraps DatabaseManager and MerchantMatcher with a simpler API
    suitable for batch scripts and interactive use.
    """

    def __init__(self, db_path: Optional[str] = None,
                 use_aliases: bool = True):
        self.db = DatabaseManager(db_path)
        self.matcher = MerchantMatcher(self.db, use_aliases=use_aliases)

    # ── Full Search ──────────────────────────────────────────────────────

    def search(self, query: str,
               limit: int = 50,
               min_score: float = 0) -> list:
        """Run a full search with compound expansion and weighted scoring.

        Returns a list of SearchResult objects (from matcher.py).
        Each result has .overall_score, .record, .match_type, etc.
        """
        return self.matcher.search(query, limit=limit, min_score=min_score)

    # ── Token Breakdown Search ──────────────────────────────────────────

    def token_breakdown_search(self, query: str,
                                limit: int = 10) -> Dict[str, Any]:
        """Break a query into individual tokens and show per-token matches.

        Returns a dict:
          token_results: {token: [{"score": N, "name": "...", "similarity": F}, ...]}
          combined:      [{"overall": N, "name": "...", "matched_tokens": [...]}, ...]
        """
        query = query.strip()
        if not query:
            return {"token_results": {}, "combined": []}

        query = strip_query_noise(query)  # NL words must not pollute tokens
        tokens = MerchantMatcher._tokenise(query)
        if not tokens:
            tokens = [t.upper() for t in query.split()
                      if len(t) >= config.MIN_TOKEN_LENGTH]

        # Apply compound expansion so "POWERFOIL" breaks into POWER + FOIL
        expanded = self.matcher._expand_compound_tokens(tokens)
        all_tokens = list(set(tokens + expanded))

        token_results: Dict[str, list] = {}
        combined_scores: Dict[str, Dict[str, Any]] = {}

        for token in all_tokens:
            matches = self._search_single_token(token, limit)
            token_results[token] = matches

            # Accumulate into combined scores
            for m in matches:
                name = m["name"]
                if name not in combined_scores:
                    combined_scores[name] = {
                        "overall": 0.0,
                        "name": name,
                        "matched_tokens": [],
                        "_score_sum": 0.0,
                    }
                combined_scores[name]["_score_sum"] += m["score"]
                combined_scores[name]["matched_tokens"].append(token)

        # Compute combined overall scores (average of per-token scores)
        combined_list = []
        for name, info in combined_scores.items():
            n_tokens = len(info["matched_tokens"])
            info["overall"] = round(info["_score_sum"] / n_tokens, 1) if n_tokens > 0 else 0.0
            del info["_score_sum"]
            combined_list.append(info)

        # Sort: more matched tokens first, then higher score
        combined_list.sort(key=lambda x: (-len(x["matched_tokens"]), -x["overall"]))

        return {
            "token_results": token_results,
            "combined": combined_list[:limit * 2],
        }

    # ── Internal: Per-token search ───────────────────────────────────────

    def _search_single_token(self, token: str,
                              limit: int = 10) -> List[Dict[str, Any]]:
        """Search the database for a single token, returning scored matches.

        Returns list of {"score": N, "name": "...", "similarity": F}
        """
        matches: List[Dict[str, Any]] = []
        seen_names: set = set()

        if not token or len(token) < config.MIN_TOKEN_LENGTH:
            return matches

        # 1. Column LIKE search first (high confidence, exact token match)
        col_rows = self.db.search_by_column("merchant_name", token, limit=limit * 2)
        for row in col_rows:
            name = str(row.get("merchant_name", "") or "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            sim = self._best_token_similarity(token, name)
            score = round(max(sim * 100, 70.0), 1)
            matches.append({
                "score": score,
                "name": name,
                "similarity": round(sim, 3),
                "tid": row.get("tid") or "",
                "mxcode": row.get("mxcode") or "",
            })

        # 2. FTS search (supplement with fuzzy matches)
        fts_rows = self.db.search_fts(token, limit=limit * 2)
        for row in fts_rows:
            name = str(row.get("merchant_name", "") or "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            sim = self._best_token_similarity(token, name)
            score = round(min(sim * 100, 100.0), 1)
            matches.append({
                "score": score,
                "name": name,
                "similarity": round(sim, 3),
                "tid": row.get("tid") or "",
                "mxcode": row.get("mxcode") or "",
            })

        # Sort by score descending
        matches.sort(key=lambda x: -x["score"])
        return matches[:limit]

    # ── Internal: Token similarity against name tokens ──────────────

    @staticmethod
    def _best_token_similarity(token: str, merchant_name: str) -> float:
        """
        Compare a query token against each individual word in the merchant
        name and return the best similarity score (0.0 - 1.0).

        This is better than comparing against the full name because
        "BEACON" vs "BEACONHEALTH - SANGOTEDO" (25 chars) is a poor
        comparison — we want "BEACON" vs "BEACONHEALTH" (11 chars).
        """
        if not token or not merchant_name:
            return 0.0
        qt = token.upper()
        # Split merchant name into individual words
        name_tokens = merchant_name.upper().split()
        best = 0.0
        for nt in name_tokens:
            # Exact match on a single name token → perfect score
            if qt == nt:
                return 1.0
            # Fuzzy ratio (rapidfuzz-backed)
            ratio = fuzzy_ratio(qt, nt)
            if ratio > best:
                best = ratio
            # Substring: token is part of name token or vice versa
            if qt in nt or nt in qt:
                bonus = min(ratio + 0.15, 1.0)
                if bonus > best:
                    best = bonus
            # Phonetic (Metaphone) — catches transliteration drift, capped
            # at 0.92 so phonetic evidence alone never yields a perfect score.
            ph_sim = phonetic_similarity(qt, nt)
            if ph_sim >= 0.85 and ph_sim > best:
                best = min(ph_sim, 0.92)
        return best

    def __repr__(self):
        return f"<MerchantSearch db={self.db.db_path}>"
