"""rag.py — Retrieval-Augmented Generation grounding for the Merchant Copilot.

Provides the copilot with grounded, citeable facts from the merchant database
so its answers are traceable to source data rather than hallucinated.

Architecture:
  1. **Query analysis** — extract entities (merchant names, TIDs, MX codes,
     identifiers) and intent from the user's question.
  2. **Retrieval** — search the database for matching merchants and their
     related records (identifiers, static accounts, addresses, etc.).
  3. **Context assembly** — format retrieved records into a structured
     context window with provenance metadata.
  4. **Citation** — every fact in the copilot's response is tagged with a
     source reference (merchant_name, field, source_file, row).

The RAG module does NOT call any LLM — it provides the *grounding* that
the copilot wraps in natural language.  This separation means:
  - The copilot can work with any LLM (OpenAI, local, none at all).
  - The grounding is deterministic and auditable.
  - Facts are never hallucinated — they come from the DB.

Usage::

    from merchant_intelligence.rag import retrieve_context
    ctx = retrieve_context("what is the static account for MEDPLUS LEKKI")
    # ctx["documents"] = [{text, source, confidence, ...}, ...]
    # ctx["entities"] = [{type, value, ...}, ...]
    # ctx["query_analysis"] = {intent, entities, ...}
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


def _db_path() -> Path:
    override = os.environ.get("MERCHANT_INTELLIGENCE_DB")
    return Path(override) if override else _DATA_DIR / "intelligence.db"


# ── Entity extraction ───────────────────────────────────────────────────────

# Identifier patterns
_TID_RE = re.compile(r"\b2\w{6,8}\b", re.I)
_MX_RE = re.compile(r"\bMX\d{4,7}\b", re.I)
_MID_RE = re.compile(r"\b\d{6,15}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.I)
_PHONE_RE = re.compile(r"\b0[789]\d{9}\b")

# Intent keywords
_INTENT_KW = {
    "static_account": ["static account", "static acct", "beneficiary", "payable",
                       "alias", "acct manager", "settle"],
    "profile": ["profile", "full profile", "merchant details"],
    "tid": ["tid", "terminal", "device", "pos machine"],
    "mxcode": ["mxcode", "mx code", "merchant code"],
    "email": ["email", "e-mail", "mail", "inbox"],
    "phone": ["phone", "mobile", "telephone", "call", "number", "reach", "dial"],
    "address": ["address", "location", "where", "situated", "based", "street"],
    "bank": ["bank", "bank name", "banking"],
    "change_details": ["change", "old and new", "previous", "history", "switch"],
    "settlement_account": ["settlement account", "dealer account", "settle"],
    "settlement_bank": ["settlement bank", "dealer bank"],
    "merchant_id": ["merchant id", "internal id", "identification"],
    "dealer_name": ["dealer name", "trading name", "operator"],
}


@dataclass
class ExtractedEntity:
    type: str      # "tid" | "mxcode" | "mid" | "email" | "phone" | "name"
    value: str
    confidence: float = 1.0


@dataclass
class QueryAnalysis:
    intent: str
    entities: List[ExtractedEntity]
    raw_text: str


def extract_entities(text: str) -> List[ExtractedEntity]:
    """Extract identifier entities from natural language text."""
    entities = []
    for m in _TID_RE.finditer(text):
        entities.append(ExtractedEntity("tid", m.group().upper()))
    for m in _MX_RE.finditer(text):
        entities.append(ExtractedEntity("mxcode", m.group().upper()))
    for m in _EMAIL_RE.finditer(text):
        entities.append(ExtractedEntity("email", m.group().lower()))
    for m in _PHONE_RE.finditer(text):
        entities.append(ExtractedEntity("phone", m.group()))
    return entities


def detect_intent(text: str) -> str:
    """Detect the most likely intent from the user's query."""
    low = text.lower()
    best_intent = "profile"
    best_score = 0
    for intent, keywords in _INTENT_KW.items():
        score = sum(1 for kw in keywords if kw in low)
        if score > best_score:
            best_score = score
            best_intent = intent
    return best_intent


def analyze_query(text: str) -> QueryAnalysis:
    """Full query analysis: entities + intent."""
    entities = extract_entities(text)
    intent = detect_intent(text)
    return QueryAnalysis(intent=intent, entities=entities, raw_text=text)


# ── Retrieval ───────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(_db_path()))


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def retrieve_merchants(
    entities: List[ExtractedEntity],
    name_hint: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Retrieve merchant records matching the extracted entities."""
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        merchants = []
        seen_ids = set()

        # Search by identifier entities
        for ent in entities:
            if ent.type == "tid":
                rows = conn.execute(
                    "SELECT * FROM merchants WHERE UPPER(TRIM(tid)) = ?",
                    (ent.value.upper(),)).fetchall()
            elif ent.type == "mxcode":
                rows = conn.execute(
                    "SELECT * FROM merchants WHERE UPPER(TRIM(mxcode)) = ?",
                    (ent.value.upper(),)).fetchall()
            elif ent.type == "email":
                rows = conn.execute(
                    "SELECT * FROM merchants WHERE LOWER(TRIM(email)) = ?",
                    (ent.value.lower(),)).fetchall()
            elif ent.type == "phone":
                rows = conn.execute(
                    "SELECT * FROM merchants WHERE TRIM(phone) = ?",
                    (ent.value,)).fetchall()
            else:
                rows = []

            for r in rows:
                d = _row_to_dict(r)
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
                    merchants.append(d)

        # If no entity matches, try name search
        if not merchants and name_hint:
            rows = conn.execute(
                "SELECT * FROM merchants "
                "WHERE UPPER(merchant_name) LIKE ? "
                "ORDER BY quality_score DESC LIMIT ?",
                (f"%{name_hint.upper()}%", limit)).fetchall()
            for r in rows:
                d = _row_to_dict(r)
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
                    merchants.append(d)

        return merchants[:limit]
    finally:
        conn.close()


def retrieve_identifiers(merchant_id: int) -> List[Dict[str, Any]]:
    """Retrieve all identifiers for a merchant from the normalized table."""
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM identifiers WHERE merchant_id = ?",
            (merchant_id,)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def retrieve_cluster(cluster_id: str) -> List[Dict[str, Any]]:
    """Retrieve all merchants in an entity cluster."""
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ec.*, m.merchant_name, m.tid, m.mxcode, m.phone, m.email "
            "FROM entity_clusters ec "
            "JOIN merchants m ON ec.merchant_id = m.id "
            "WHERE ec.cluster_id = ?",
            (cluster_id,)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── Context assembly ────────────────────────────────────────────────────────

def _format_merchant_doc(merchant: Dict[str, Any]) -> Dict[str, Any]:
    """Format a merchant record into a RAG document with provenance."""
    fields = []
    for key in ["merchant_name", "tid", "mxcode", "phone", "email",
                "address", "contact_name", "account_name", "account_number",
                "bank", "state", "static_acc_no", "alias", "payable_code"]:
        val = merchant.get(key)
        if val and str(val).strip():
            fields.append(f"{key}: {val}")

    return {
        "text": " | ".join(fields),
        "source": {
            "merchant_name": merchant.get("merchant_name", ""),
            "tid": merchant.get("tid", ""),
            "mxcode": merchant.get("mxcode", ""),
            "sheet": merchant.get("sheet_name", ""),
            "row": merchant.get("row_number", ""),
        },
        "confidence": merchant.get("quality_score", 1.0) or 1.0,
        "fields": {k: merchant.get(k) for k in [
            "merchant_name", "tid", "mxcode", "phone", "email",
            "address", "contact_name", "account_name", "account_number",
            "bank", "state", "static_acc_no", "alias", "payable_code",
        ] if merchant.get(k)},
    }


def retrieve_context(
    query: str,
    max_docs: int = 10,
) -> Dict[str, Any]:
    """Full RAG pipeline: analyze query → retrieve → assemble context.

    Returns a context dict with:
      - query_analysis: detected intent + entities
      - documents: retrieved merchant records formatted as citeable docs
      - entities: extracted identifier entities
      - summary: human-readable summary of what was found
    """
    analysis = analyze_query(query)

    # Try to extract a merchant name from the query (text not matched as identifier)
    name_hint = None
    low = query.lower()
    # Remove common noise words and identifier-like patterns
    cleaned = re.sub(r"\b(get|show|find|pull|fetch|give|me|the|for|all|of|and|from|to|what|is|are|where|how|please|assist|help)\b",
                     " ", low)
    cleaned = re.sub(r"\b2\w{6,8}\b", " ", cleaned)  # remove TIDs
    cleaned = re.sub(r"\bMX\d+\b", " ", cleaned)  # remove MX codes
    cleaned = re.sub(r"\b\d{6,15}\b", " ", cleaned)  # remove numeric IDs
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)  # remove punctuation
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # If meaningful text remains, it's likely a merchant name
    intent_words = set()
    for kws in _INTENT_KW.values():
        intent_words.update(kws)
    intent_words.update({"get", "show", "find", "pull", "fetch", "give", "me",
                         "the", "for", "all", "of", "and", "from", "to", "what",
                         "is", "are", "where", "how", "please", "assist", "help"})

    name_tokens = [t for t in cleaned.split() if t not in intent_words and len(t) > 1]
    if name_tokens:
        name_hint = " ".join(name_tokens)

    # Retrieve matching merchants
    merchants = retrieve_merchants(analysis.entities, name_hint, limit=max_docs)

    # Format as RAG documents
    documents = [_format_merchant_doc(m) for m in merchants]

    # Build summary
    n = len(documents)
    if n == 0:
        summary = f"No merchants found matching your query about {analysis.intent}."
    elif n == 1:
        summary = f"Found 1 merchant: {documents[0]['source']['merchant_name']}."
    else:
        names = [d["source"]["merchant_name"] for d in documents[:3]]
        summary = f"Found {n} merchants: {', '.join(names)}"
        if n > 3:
            summary += f" (and {n-3} more)"

    return {
        "query_analysis": asdict(analysis),
        "documents": documents,
        "entities": [asdict(e) for e in analysis.entities],
        "summary": summary,
        "n_results": n,
        "intent": analysis.intent,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, str(_PROJECT_ROOT))

    test_queries = [
        "get the static account for MEDPLUS",
        "what is the phone number for 2ISW916B",
        "show me the profile of ADDIDE APATA",
        "get me the settlement account for 2103O380",
    ]
    for q in test_queries:
        print(f"\n{'='*60}")
        print(f"Query: {q}")
        ctx = retrieve_context(q)
        print(f"Intent: {ctx['intent']}")
        print(f"Entities: {ctx['entities']}")
        print(f"Results: {ctx['n_results']}")
        print(f"Summary: {ctx['summary']}")
        for doc in ctx["documents"][:2]:
            print(f"  📄 {doc['text'][:80]}...")
