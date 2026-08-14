"""
aliases.py — Merchant alias engine.

Provides alias generation and lookup for merchant names.
Used by debug_search.py for diagnostic purposes.
"""
import json
import logging
import re
from typing import Dict, List, Optional, Set

from . import config

logger = logging.getLogger(__name__)


class AliasEngine:
    """Generates and manages merchant name aliases.

    Automatically creates aliases by stripping generic words
    (LTD, LIMITED, NIGERIA, etc.) and supports manual alias
    overrides from config.MANUAL_ALIASES.

    Phase 10 — Auto-learning: confirmed matches are persisted to
    config.ALIAS_CACHE_FILE (merchant_aliases.json) and loaded back on
    every start, so the engine gets smarter across runs.
    """

    def __init__(self):
        self.generic_words: Set[str] = {
            w.upper() for w in config.GENERIC_WORDS
        }
        self.manual_aliases: Dict[str, List[str]] = {
            k.upper(): [a.upper() for a in v]
            for k, v in config.MANUAL_ALIASES.items()
        }
        # Auto-learned aliases: canonical -> [aliases] (loaded from JSON)
        self.learned_aliases: Dict[str, List[str]] = {}
        # Approved learned aliases: "CANONICAL|ALIAS" keys (review queue)
        self.approved_aliases: Set[str] = set()
        self._load_learned()
        logger.debug("AliasEngine initialised with %d manual alias sets",
                     len(self.manual_aliases))

    # ── Phase 10: Auto-learning persistence ──────────────────────────────

    def learn(self, alias: str, canonical: str) -> bool:
        """Record a confirmed alias→canonical mapping and persist it.

        Called when a user confirms a match (e.g. in the web UI) or when
        entity resolution discovers a confident relationship.
        Returns True if a NEW mapping was stored.
        """
        alias_u = (alias or "").upper().strip()
        canonical_u = (canonical or "").upper().strip()
        if not alias_u or not canonical_u or alias_u == canonical_u:
            return False

        existing = self.learned_aliases.setdefault(canonical_u, [])
        if alias_u in existing:
            return False
        existing.append(alias_u)

        # Also make it usable immediately in manual lookup
        self.manual_aliases.setdefault(canonical_u, [])
        if alias_u not in self.manual_aliases[canonical_u]:
            self.manual_aliases[canonical_u].append(alias_u)

        self._save_learned()
        logger.info("🧠 Learned alias: %s -> %s", alias_u, canonical_u)
        return True

    def forget(self, alias: str, canonical: Optional[str] = None) -> bool:
        """Remove a learned alias mapping (used for corrections / rejection).

        Purges the mapping from both the persisted learned cache and the
        in-memory manual lookup table so it stops matching immediately.
        """
        alias_u = (alias or "").upper().strip()
        if not alias_u:
            return False
        removed = False
        for canon, aliases in list(self.learned_aliases.items()):
            if canonical and canon != canonical.upper().strip():
                continue
            if alias_u in aliases:
                aliases.remove(alias_u)
                removed = True
            self.approved_aliases.discard(f"{canon}|{alias_u}")
            if not aliases:
                self.learned_aliases.pop(canon, None)
            # Purge from the live manual lookup table too
            if canon in self.manual_aliases and alias_u in self.manual_aliases[canon]:
                self.manual_aliases[canon].remove(alias_u)
            if canon in self.manual_aliases and not self.manual_aliases[canon]:
                self.manual_aliases.pop(canon, None)
        if removed:
            self._save_learned()
        return removed

    def approve(self, alias: str, canonical: str) -> bool:
        """Mark a learned alias as approved (review queue).

        Approved aliases are kept in the persisted cache under the approved
        list, so the review queue can show pending vs approved items.
        Returns True when the alias now exists and is marked approved.
        """
        alias_u = (alias or "").upper().strip()
        canon_u = (canonical or "").upper().strip()
        if not alias_u or not canon_u:
            return False
        existing = self.learned_aliases.setdefault(canon_u, [])
        if alias_u not in existing:
            existing.append(alias_u)
        self.approved_aliases.add(f"{canon_u}|{alias_u}")
        self.manual_aliases.setdefault(canon_u, [])
        if alias_u not in self.manual_aliases[canon_u]:
            self.manual_aliases[canon_u].append(alias_u)
        self._save_learned()
        return True

    def review_items(self) -> List[Dict[str, str]]:
        """All learned aliases with their review status, for the queue UI."""
        items: List[Dict[str, str]] = []
        for canon, aliases in self.learned_aliases.items():
            for a in aliases:
                items.append({
                    "canonical": canon,
                    "alias": a,
                    "status": ("approved"
                               if f"{canon}|{a}" in self.approved_aliases
                               else "pending"),
                })
        return items

    @staticmethod
    def manual_items() -> List[Dict[str, str]]:
        """Aliases defined in config.MANUAL_ALIASES (source: manual)."""
        items: List[Dict[str, str]] = []
        for canon, aliases in config.MANUAL_ALIASES.items():
            for a in aliases:
                items.append({
                    "canonical": canon.upper(),
                    "alias": str(a).upper(),
                    "status": "manual",
                })
        return items

    def _load_learned(self):
        try:
            path = config.ALIAS_CACHE_FILE
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            # New format: {"aliases": {...}, "approved": [...]}
            if isinstance(data, dict) and "aliases" in data:
                aliases_map = data["aliases"] or {}
                approved = data.get("approved") or []
                self.approved_aliases = {str(a).upper() for a in approved}
            else:
                # Legacy format: flat {canonical: [aliases]} — treat all as pending
                aliases_map = data
                self.approved_aliases = set()
            for canonical, aliases in aliases_map.items():
                canon_u = canonical.upper().strip()
                if not isinstance(aliases, list):
                    continue
                self.learned_aliases[canon_u] = [a.upper() for a in aliases]
                self.manual_aliases.setdefault(canon_u, [])
                for a in aliases:
                    a_u = a.upper()
                    if a_u not in self.manual_aliases[canon_u]:
                        self.manual_aliases[canon_u].append(a_u)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not load learned aliases: %s", exc)

    def _save_learned(self):
        try:
            path = config.ALIAS_CACHE_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "aliases": self.learned_aliases,
                    "approved": sorted(self.approved_aliases),
                }, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not save learned aliases: %s", exc)

    def generate_aliases(self, name: str) -> List[str]:
        """Generate alias variants for a merchant name.

        Strips generic words (LTD, LIMITED, NIGERIA, etc.) in multiple
        passes to produce shorter variants.
        """
        if not name:
            return []

        name_upper = name.upper().strip()
        aliases: Set[str] = set()
        aliases.add(name.strip())
        aliases.add(name_upper)

        tokens = name_upper.split()

        # Pass 1: remove all generic words
        filtered = [t for t in tokens if t not in self.generic_words]
        if filtered and filtered != tokens:
            aliases.add(" ".join(filtered))

        # Pass 2: iterative removal (remove one generic at a time)
        current = tokens[:]
        for _ in range(len(tokens)):
            new_tokens = [t for t in current if t not in self.generic_words]
            if len(new_tokens) < len(current) and new_tokens:
                aliases.add(" ".join(new_tokens))
                # Also add each individual significant token
                for t in new_tokens:
                    if len(t) >= config.MIN_TOKEN_LENGTH:
                        aliases.add(t)
                current = new_tokens
            else:
                break

        # Pass 3: strip generic words as substrings in the full name
        for w in self.generic_words:
            if w in name_upper:
                stripped = re.sub(r"\s*" + re.escape(w) + r"\s*", " ",
                                  name_upper).strip()
                if stripped and stripped != name_upper:
                    aliases.add(stripped)
                    sub_tokens = [
                        t for t in stripped.split()
                        if t not in self.generic_words
                        and len(t) >= config.MIN_TOKEN_LENGTH
                    ]
                    if sub_tokens:
                        aliases.add(" ".join(sub_tokens))

        return sorted(a for a in aliases if len(a) >= config.MIN_TOKEN_LENGTH)

    def lookup(self, name: str) -> Optional[str]:
        """Check if a name matches any known merchant via manual aliases.

        Returns the canonical merchant name if found, or None.
        """
        name_upper = name.upper().strip()
        # Direct match
        if name_upper in self.manual_aliases:
            return name_upper

        # Check if name matches any alias
        for canonical, aliases in self.manual_aliases.items():
            if name_upper in aliases:
                return canonical
            # Fuzzy: check if canonical appears in name or vice versa
            if (canonical in name_upper or name_upper in canonical):
                return canonical

        return None

    def __repr__(self):
        return f"<AliasEngine manuals={len(self.manual_aliases)}>"
