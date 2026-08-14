"""
database.py — SQLite FTS5 database manager.

Provides:
  - DatabaseManager class with FTS5 search and column-based LIKE search
  - Connection lifecycle management
  - FTS5 query sanitisation
  - Normalized name buckets (instant exact-normalized lookup + autocomplete)
"""
import logging
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from . import config
from .fuzzy import canonicalize

logger = logging.getLogger(__name__)

# Columns that can be used in LIKE searches
SEARCHABLE_COLUMNS = [
    "merchant_name", "slip_header", "email", "phone", "address",
    "contact_name", "account_name", "alias", "mxcode",
    "payable_code", "tid", "terminal_serial", "remarks",
    "account_number", "merchant_id",
]


def build_name_buckets(conn) -> int:
    """Build (or rebuild) the name_buckets table from the merchants table.

    A bucket key is the canonicalized merchant name with generic words
    stripped ("LAGOON WATERS LTD" → "LAGOON WATERS"), mapped to the
    comma-separated ids of every row sharing it. Used by both build scripts
    (rebuild_db.py, build_intelligence_db.py) and lazily by
    DatabaseManager.ensure_buckets() for existing databases.

    Returns the number of distinct bucket keys written.
    """
    generics = {w.upper() for w in config.GENERIC_WORDS}
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS name_buckets (
        bucket_key TEXT PRIMARY KEY,
        ids TEXT
    )""")
    buckets: Dict[str, List[str]] = {}
    for row in c.execute("SELECT id, merchant_name FROM merchants"):
        key = DatabaseManager._bucket_key(row[1] or "")
        if not key:
            continue
        buckets.setdefault(key, []).append(str(row[0]))
    c.execute("DELETE FROM name_buckets")
    c.executemany(
        "INSERT OR REPLACE INTO name_buckets(bucket_key, ids) VALUES (?, ?)",
        [(k, ",".join(v)) for k, v in buckets.items()],
    )
    conn.commit()
    return len(buckets)


class DatabaseManager:
    """Manages the SQLite FTS5 merchant database."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # App loads the active database — intelligence.db when built,
            # legacy merchant_search.db otherwise (see config.active_db).
            db_path = str(config.active_db())
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._has_trigram: Optional[bool] = None   # cache schema check
        self._has_buckets: Optional[bool] = None   # cache schema check
        self._bucket_keys: Optional[List[str]] = None  # cache of distinct bucket keys
        self._row_count: Optional[int] = None          # cache of total row count
        # Serialises cursor use on the shared connection. ``check_same_thread=False``
        # lets any worker thread touch the connection, but SQLite releases the GIL
        # during calls — without this lock two concurrent requests could run cursors
        # on the same connection at once ("recursive use of cursors" flakes).
        self._lock = threading.RLock()
        logger.debug("DatabaseManager initialised with path: %s", self.db_path)

    # ── Connection ───────────────────────────────────────────────────────

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create the database connection.

        ``check_same_thread=False`` is required because FastAPI runs each
        sync endpoint in a worker thread from its thread pool, so a request
        can land on a different thread than the one that created this
        connection. Without it every search would raise
        ``sqlite3.ProgrammingError: SQLite objects created in a thread can
        only be used in that same thread``. Reads here are single-statement
        (no transactions spanning requests), so cross-thread use is safe.
        """
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
            return self._conn

    def connect(self) -> sqlite3.Connection:
        """Return the raw sqlite3 connection for direct queries."""
        return self._get_connection()

    def close(self):
        """Close the database connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Full-Text Search (FTS5) ──────────────────────────────────────────

    def search_fts(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Full-text search using the FTS5 virtual table.

        Returns a list of row dicts (including id, merchant_name, etc.)
        ordered by FTS5 rank (best match first).
        Returns empty list on failure.
        """
        safe = self._sanitise_fts_query(query)
        if not safe:
            return []

        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute("""
                    SELECT m.*, rank
                    FROM merchants m
                    JOIN merchants_fts fts ON m.id = fts.rowid
                    WHERE merchants_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (safe, limit))
                return [dict(row) for row in c.fetchall()]
            except sqlite3.OperationalError as exc:
                logger.warning("FTS5 query failed: %s — query=%r", exc, safe)
                return []

    # ── Trigram Full-Text Search ─────────────────────────────────────────
    # A second FTS5 index (tokenize='trigram') provides substring + typo-tolerant
    # matching that the word-level 'porter unicode61' index cannot:
    #   e.g. "POWERFOIL" finds "POWERFOIL GLOBAL SERVICES" even with typos,
    #        "INTERNMATIONAL" finds "INTERNATIONAL SCHOOL" via substring.

    def has_trigram_index(self) -> bool:
        """Check whether the trigram FTS table exists in this database.

        Cached after the first check — the schema does not change at runtime.
        """
        if self._has_trigram is None:
            with self._lock:
                conn = self._get_connection()
                c = conn.cursor()
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='merchants_fts_trigram'")
                self._has_trigram = c.fetchone() is not None
        return self._has_trigram

    def search_fts_trigram(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Substring-tolerant full-text search using the trigram FTS5 index.

        Each query token (>= 3 chars) is matched independently, so:
          - "POWERFOIL" matches a stored "POWERFOIL GLOBAL SERVICES"
          - "INTERNMATIONAL" (typo) still matches "INTERNATIONAL SCHOOL"
          - Multi-token typo queries still match on the tokens that DO match.
        Returns [] if the trigram table is missing or no usable tokens.
        """
        if not self.has_trigram_index():
            return []

        # Tokenise: keep only word tokens >= 3 chars (trigram minimum),
        # dropping generic/stop words (THE, AND, LIMITED…) so common tokens
        # don't flood the candidate pool or wrongly exclude valid matches.
        generic = {w.lower() for w in config.GENERIC_WORDS}
        tokens = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower())
                  if len(t) >= 3 and t not in generic]
        if not tokens:
            return []

        # Match each token independently (AND), so one typo'd token does not
        # sink the whole query. Trigram finds substring matches per token.
        match_query = " AND ".join(f'"{t}"' for t in tokens[:10])

        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute("""
                    SELECT m.*, rank
                    FROM merchants m
                    JOIN merchants_fts_trigram fts ON m.id = fts.rowid
                    WHERE merchants_fts_trigram MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (match_query, limit))
                return [dict(row) for row in c.fetchall()]
            except sqlite3.OperationalError as exc:
                logger.warning("Trigram FTS query failed: %s — query=%r", exc, match_query)
                return []

    # ── Normalized name buckets ──────────────────────────────────────────
    # A bucket is a canonicalized, generic-stripped merchant name
    # ("LAGOON WATERS LTD" -> "LAGOON WATERS") mapped to the row ids that
    # share it. Exact bucket lookup is O(1) (one indexed row) and powers
    # both instant exact-normalized search and autocomplete.

    @staticmethod
    def _bucket_key(name: str) -> str:
        """Canonical bucket key: canonicalize + strip generic words."""
        canon = canonicalize(name or "")
        if not canon:
            return ""
        generics = {w.upper() for w in config.GENERIC_WORDS}
        kept = [t for t in canon.split() if t not in generics]
        return " ".join(kept)

    def has_buckets(self) -> bool:
        """Check whether the name_buckets table exists (cached)."""
        if self._has_buckets is None:
            with self._lock:
                conn = self._get_connection()
                c = conn.cursor()
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='name_buckets'")
                self._has_buckets = c.fetchone() is not None
        return self._has_buckets

    def ensure_buckets(self):
        """Build the name_buckets table if it does not exist yet.

        Idempotent — a second call is a no-op. Runs one full scan of the
        merchants table the first time (a second or two on this registry),
        then every lookup is an indexed single-row read.
        """
        if self.has_buckets():
            return
        with self._lock:
            n = build_name_buckets(self._get_connection())
            self._has_buckets = True
        logger.debug("name_buckets built with %d keys", n)

    def lookup_bucket(self, key: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Return the full rows for an exact canonical bucket key (instant)."""
        if not key:
            return []
        if not self.has_buckets():
            return []
        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("SELECT ids FROM name_buckets WHERE bucket_key = ?", (key,))
            row = c.fetchone()
            if not row or not row[0]:
                return []
            ids = [int(i) for i in row[0].split(",") if i.strip()][:limit]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            c.execute(f"SELECT * FROM merchants WHERE id IN ({placeholders})", ids)
            return [dict(r) for r in c.fetchall()]

    def count_rows(self) -> int:
        """Total row count (cached after first call — the table is static)."""
        if self._row_count is None:
            with self._lock:
                conn = self._get_connection()
                self._row_count = conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
        return self._row_count

    def count_tokens(self, column: str, token: str) -> int:
        """Approximate row count mentioning `token` — a rarity signal, not a
        per-column truth.

        Fast path: COUNT(*) on the trigram FTS index (~30ms, substring-
        tolerant). NOTE: the trigram FTS table spans EVERY searchable column,
        so the fast-path count is registry-wide (token appears anywhere), not
        limited to `column`. Falls back to a per-column LIKE COUNT(*) over
        the table (one pass, ~0.5s). Both are one-shot per unique token and
        the matcher caches the results. Used for token-rarity (IDF) weighting
        and compound-expansion viability, where a registry-wide frequency is
        the right signal.
        """
        token = (token or "").strip()
        if not token:
            return 0
        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            if self.has_trigram_index():
                try:
                    return c.execute(
                        "SELECT COUNT(*) FROM merchants_fts_trigram "
                        "WHERE merchants_fts_trigram MATCH ?",
                        (f'"{token}"',),
                    ).fetchone()[0]
                except sqlite3.OperationalError as exc:
                    logger.warning("Trigram COUNT failed: %s", exc)
            try:
                return c.execute(
                    f"SELECT COUNT(*) FROM merchants WHERE {column} LIKE ?",
                    (f"%{token}%",),
                ).fetchone()[0]
            except sqlite3.OperationalError as exc:
                logger.warning("Token COUNT failed: %s", exc)
                return 0

    def bucket_keys(self) -> List[str]:
        """All distinct canonical bucket keys (cached after first load).

        Far fewer than rows (distinct canonical names vs 76k rows), so a fuzzy
        scan over these recovers near-exact names cheaply."""
        if not self.has_buckets():
            return []
        if self._bucket_keys is None:
            with self._lock:
                conn = self._get_connection()
                rows = conn.execute("SELECT bucket_key FROM name_buckets").fetchall()
            self._bucket_keys = [r[0] for r in rows]
        return self._bucket_keys

    def autocomplete(self, prefix: str, limit: int = 8) -> List[str]:
        """Return merchant bucket keys whose canonical key starts with prefix.

        Prefix is canonicalized the same way as stored keys, so typing
        "lagoon wat" returns the "LAGOON WATERS" bucket. Cheap: one LIKE
        query on the bucket_key primary key.
        """
        if not prefix or not self.has_buckets():
            return []
        key_prefix = DatabaseManager._bucket_key(prefix)
        if not key_prefix:
            return []
        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                "SELECT bucket_key FROM name_buckets WHERE bucket_key LIKE ? "
                "ORDER BY length(bucket_key) LIMIT ?",
                (key_prefix + "%", limit),
            )
            return [r[0] for r in c.fetchall()]

    # ── Column LIKE Search (fallback) ────────────────────────────────────

    def search_by_column(self, column: str, token: str,
                         limit: int = 50) -> List[Dict[str, Any]]:
        """
        Search a specific column using LIKE '%token%'.

        Raises ValueError if the column name is not in the allowed list
        (to prevent SQL injection via column name).
        """
        if column not in SEARCHABLE_COLUMNS:
            raise ValueError(
                f"Column '{column}' is not in the allowed search columns"
            )

        with self._lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute(
                    f"SELECT * FROM merchants WHERE {column} LIKE ? LIMIT ?",
                    (f"%{token}%", limit),
                )
                return [dict(row) for row in c.fetchall()]
            except sqlite3.OperationalError as exc:
                logger.warning("Column search failed: %s — col=%r token=%r",
                               exc, column, token)
                return []

    # ── FTS Query Sanitisation ───────────────────────────────────────────

    @staticmethod
    def _sanitise_fts_query(query: str) -> str:
        """
        Sanitise a user query string for FTS5 MATCH syntax.

        Removes special FTS5 characters, normalises whitespace, and
        wraps each token in double quotes for phrase matching.
        """
        if not query or not query.strip():
            return ""

        # Remove characters that have special meaning in FTS5 syntax
        # (^, *, ", (, ), +, -, ~, etc.) — keep only word characters and spaces
        clean = re.sub(r"[^\w\s]", " ", query)
        clean = re.sub(r"\s+", " ", clean).strip()

        if not clean:
            return ""

        tokens = clean.split()
        # Wrap each token in double quotes to treat them as literal terms
        return " AND ".join(f'"{t}"' for t in tokens)

    def __repr__(self):
        return f"<DatabaseManager path={self.db_path}>"
