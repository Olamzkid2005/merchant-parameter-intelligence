"""
merchant_intelligence — Merchant Parameter Search Engine.

A modular toolkit for searching, matching, and analysing merchant records
from Excel workbooks using SQLite FTS5 full-text search, token-based
fuzzy matching, compound word expansion, and weighted scoring.

Main components:
  - DatabaseManager  — SQLite FTS5 database wrapper (database.py)
  - MerchantMatcher  — Token-based fuzzy matcher with scoring (matcher.py)
  - MerchantSearch   — High-level search + token breakdown (search.py)
  - AliasEngine      — Merchant alias generation and lookup (aliases.py)
  - config           — Paths, weights, thresholds, compound lists (config.py)
"""
import logging

from .aliases import AliasEngine
from .database import DatabaseManager
from .entity import EntityResolver
from .matcher import MerchantMatcher, SearchResult
from .search import MerchantSearch

__all__ = [
    "AliasEngine",
    "DatabaseManager",
    "EntityResolver",
    "MerchantMatcher",
    "MerchantSearch",
    "SearchResult",
]

# Set up a default null handler so importing the package doesn't
# trigger "No handler found" warnings.  Callers can configure
# their own logging as needed.
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
