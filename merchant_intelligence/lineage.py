"""lineage.py — source lineage queries (roadmap #2, final slice).

Closes the governed-data-platform loop: every merchant row is traceable to
the exact workbook, sheet, physical spreadsheet row, and file version
(content hash) it was ingested from — and every ingested file is traceable
forward to the rows it produced.

How the chain is stored
    - ``merchants.sheet_name``  "<file stem> :: <sheet>"  (build-time)
    - ``merchants.row_number``  physical row in the sheet (build-time)
    - ``merchants.source_file_id`` -> ``source_files.id``  (v3 migration +
      stamped by build_intelligence_db at ingest time)
    - ``source_files``  file_path, content hash (sha256), row_count, column
      headers, ingested_at — the exact file VERSION that produced the rows

Fallback: rows predating the source_file_id stamp are traced through the
sheet_name -> source_files join (same file::sheet, no id). The trace always
says which resolution path was used (``link: "id" | "sheet_name" | "none"``).

All helpers take ``db_path`` (default: the active intelligence.db) so tests
run hermetically on temp databases.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_db() -> Path:
    import os
    override = os.environ.get("MERCHANT_INTELLIGENCE_DB")
    if override:
        return Path(override)
    from . import config
    return config.active_db()


def merchant_trace(merchant_id: int,
                   db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Full lineage for one merchant row.

    Returns {merchant: {...}, source_file: {...} | None, link: id|sheet_name|none}.
    """
    path = Path(db_path) if db_path else _default_db()
    if not path.exists():
        return {"ok": False, "error": f"database not found: {path}"}
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        m = conn.execute(
            "SELECT id, sheet_name, row_number, merchant_name, merchant_id,"
            " tid, mxcode, imported_at, source_file_id"
            " FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
        if m is None:
            return {"ok": False,
                    "error": f"no merchant row with id {merchant_id}"}

        sf = None
        link = "none"
        if m["source_file_id"]:
            sf = conn.execute(
                "SELECT id, file_path, file_hash, sheet_name, row_count,"
                " column_names, ingested_at, status FROM source_files"
                " WHERE id = ?", (m["source_file_id"],)).fetchone()
            if sf is not None:
                link = "id"
        if sf is None and m["sheet_name"]:
            # Fallback: sheet_name embeds the file stem; match against
            # source_files by "<stem> :: <sheet>".
            stem, sep, sheet = str(m["sheet_name"]).partition(" :: ")
            if sep:
                candidates = conn.execute(
                    "SELECT id, file_path, file_hash, sheet_name, row_count,"
                    " column_names, ingested_at, status FROM source_files"
                    " WHERE sheet_name = ?", (sheet,)).fetchall()
                for c in candidates:
                    if Path(c["file_path"]).stem == stem:
                        sf = c
                        link = "sheet_name"
                        break

        out: Dict[str, Any] = {
            "ok": True,
            "link": link,
            "merchant": {
                "id": m["id"],
                "merchant_name": m["merchant_name"],
                "merchant_id": m["merchant_id"],
                "tid": m["tid"],
                "mxcode": m["mxcode"],
                "sheet_name": m["sheet_name"],
                "row_number": m["row_number"],
                "imported_at": m["imported_at"],
            },
            "source_file": dict(sf) if sf is not None else None,
        }
        if out["source_file"]:
            # Short display hash + the file's display name.
            h = out["source_file"].get("file_hash") or ""
            out["source_file"]["hash8"] = h[:8] if h else ""
            out["source_file"]["file_name"] = Path(
                out["source_file"]["file_path"]).name
        return out
    finally:
        conn.close()


def file_summary(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Every registered source file with its forward lineage: sheets, rows
    ingested, merchants produced. Ordered by most recently ingested."""
    path = Path(db_path) if db_path else _default_db()
    if not path.exists():
        return {"ok": False, "error": f"database not found: {path}",
                "files": []}
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        files: List[Dict[str, Any]] = []
        for sf in conn.execute(
                "SELECT id, file_path, file_hash, sheet_name, row_count,"
                " ingested_at, status FROM source_files"
                " ORDER BY ingested_at DESC, file_path").fetchall():
            stamped = conn.execute(
                "SELECT COUNT(*) FROM merchants WHERE source_file_id = ?",
                (sf["id"],)).fetchone()[0]
            by_sheet = conn.execute(
                "SELECT sheet_name, COUNT(*) AS n FROM merchants"
                " WHERE source_file_id = ? GROUP BY sheet_name"
                " ORDER BY n DESC", (sf["id"],)).fetchall()
            h = sf["file_hash"] or ""
            files.append({
                "id": sf["id"],
                "file_name": Path(sf["file_path"]).name,
                "file_path": sf["file_path"],
                "hash8": h[:8] if h else "",
                "sheet": sf["sheet_name"],
                "rows_ingested": sf["row_count"],
                "merchants": stamped,
                "merchant_sheets": [
                    {"sheet": b["sheet_name"], "rows": b["n"]}
                    for b in by_sheet],
                "ingested_at": sf["ingested_at"],
                "status": sf["status"],
            })
        total_merchants = conn.execute(
            "SELECT COUNT(*) FROM merchants").fetchone()[0]
        traced = conn.execute(
            "SELECT COUNT(*) FROM merchants WHERE source_file_id IS NOT NULL"
        ).fetchone()[0]
        return {"ok": True, "files": files, "total_merchants": total_merchants,
                "traced_merchants": traced}
    finally:
        conn.close()


def file_trace(query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Forward trace for one source file (match by name/stem/path fragment):
    its source_files records plus sample merchant rows from each."""
    path = Path(db_path) if db_path else _default_db()
    if not path.exists():
        return {"ok": False, "error": f"database not found: {path}"}
    q = (query or "").strip().lower()
    if not q:
        return {"ok": False, "error": "query is required"}
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        matches = [r for r in conn.execute(
            "SELECT id, file_path, file_hash, sheet_name, row_count,"
            " ingested_at, status FROM source_files").fetchall()
            if q in Path(r["file_path"]).name.lower()
            or q in Path(r["file_path"]).stem.lower()
            or q in r["file_path"].lower()]
        if not matches:
            return {"ok": False, "error": f"no source file matches: {query}"}
        out = []
        for sf in matches:
            samples = conn.execute(
                "SELECT id, merchant_name, merchant_id, tid, mxcode,"
                " row_number FROM merchants WHERE source_file_id = ?"
                " LIMIT 5", (sf["id"],)).fetchall()
            out.append({
                "id": sf["id"],
                "file_name": Path(sf["file_path"]).name,
                "file_path": sf["file_path"],
                "hash8": (sf["file_hash"] or "")[:8],
                "sheet": sf["sheet_name"],
                "rows_ingested": sf["row_count"],
                "ingested_at": sf["ingested_at"],
                "status": sf["status"],
                "sample_merchants": [dict(s) for s in samples],
            })
        return {"ok": True, "query": query, "files": out}
    finally:
        conn.close()
