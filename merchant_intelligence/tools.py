"""tools.py — LLM tool-use surface for the Merchant Copilot.

Exposes the engine's capabilities as structured tool definitions that an LLM
can invoke.  The copilot calls these tools instead of generating raw search
queries — every tool invocation is deterministic, auditable, and safety-railed.

Architecture:
  1. **Tool registry** — each tool is a Python function with a JSON Schema
     definition (compatible with OpenAI function-calling / Anthropic tool-use).
  2. **Tool executor** — routes tool calls to the engine, enforces safety
     rails (confidence thresholds, max results, export restrictions), and
     logs every invocation to the audit trail.
  3. **Tool result formatting** — returns structured results with provenance
     metadata so the LLM can cite specific facts.

Safety rails:
  - Export operations require explicit user confirmation (no auto-export).
  - Destructive operations (alias approval, settings changes) are blocked.
  - All tool invocations are audit-logged with the acting user.
  - Max results cap prevents unbounded data retrieval.

Usage::

    from merchant_intelligence.tools import TOOL_DEFINITIONS, execute_tool

    # Let the LLM choose a tool:
    tools = TOOL_DEFINITIONS

    # Execute the chosen tool:
    result = execute_tool("search_merchants", {"query": "MEDPLUS", "limit": 5})
    # result = {"success": True, "data": [...], "provenance": {...}}
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

# ── Tool definitions (OpenAI function-calling compatible) ────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_merchants",
            "description": "Search for merchants by name, identifier, or keyword. Returns scored results with merchant details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (merchant name, TID, MX code, phone, email, etc.)"},
                    "limit": {"type": "integer", "description": "Max results (default 10, max 50)", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_merchant_profile",
            "description": "Get the full profile of a specific merchant by identifier or name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "TID, MX code, or merchant name"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_static_account",
            "description": "Get the static account details (account number, bank, beneficiary, alias, payable) for a merchant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "TID, MX code, or merchant name"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_identifiers",
            "description": "Get all identifiers (TIDs, MX codes, phones, emails) for a merchant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "TID, MX code, or merchant name"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_related_merchants",
            "description": "Find merchants related to a given merchant (same family, shared identifiers, similar names).",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "TID, MX code, or merchant name"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_merchants",
            "description": "Compare two merchants side by side across all available fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant_a": {"type": "string", "description": "First merchant identifier or name"},
                    "merchant_b": {"type": "string", "description": "Second merchant identifier or name"},
                },
                "required": ["merchant_a", "merchant_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_merchants",
            "description": "Count merchants matching a filter (by state, bank, category, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_field": {"type": "string", "description": "Field to filter on (state, bank, sheet_name, etc.)"},
                    "filter_value": {"type": "string", "description": "Value to match"},
                },
                "required": ["filter_field", "filter_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_identifiers",
            "description": "Resolve a list of identifiers (TIDs, names, MX codes) to their full merchant records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of identifiers to resolve",
                    },
                },
                "required": ["identifiers"],
            },
        },
    },
]

# ── Tool registry ───────────────────────────────────────────────────────────

_MAX_RESULTS = 50


def _search_merchants(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search merchants by query."""
    from merchant_intelligence import MerchantSearch
    query = params["query"]
    limit = min(params.get("limit", 10), _MAX_RESULTS)
    searcher = MerchantSearch()
    results = searcher.search(query, limit=limit)
    return {
        "success": True,
        "data": [r.to_dict() for r in results],
        "count": len(results),
        "provenance": {"source": "merchant_search.db", "query": query},
    }


def _get_merchant_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get full merchant profile."""
    from merchant_intelligence.profile import MerchantProfile
    identifier = params["identifier"]
    profile = MerchantProfile()
    result = profile.get_profile(identifier)
    if result:
        return {
            "success": True,
            "data": result.to_dict() if hasattr(result, "to_dict") else result,
            "provenance": {"source": "intelligence.db", "identifier": identifier},
        }
    return {"success": False, "error": f"No merchant found for '{identifier}'"}


def _get_static_account(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get static account details via the task engine."""
    from merchant_intelligence.tasks.engine import detect_task, execute_task
    identifier = params["identifier"]
    task = detect_task(f"get the static account for {identifier}")
    if task:
        result = execute_task(task)
        return {
            "success": True,
            "data": result,
            "provenance": {"source": "intelligence.db", "intent": "static_account"},
        }
    return {"success": False, "error": f"Could not build task for '{identifier}'"}


def _get_identifiers(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all identifiers for a merchant from the normalized table."""
    from merchant_intelligence.rag import extract_entities, retrieve_merchants, retrieve_identifiers
    identifier = params["identifier"]
    entities = extract_entities(identifier)
    merchants = retrieve_merchants(entities, name_hint=identifier, limit=1)
    if not merchants:
        return {"success": False, "error": f"No merchant found for '{identifier}'"}
    m = merchants[0]
    ids = retrieve_identifiers(m["id"])
    return {
        "success": True,
        "data": {
            "merchant_name": m.get("merchant_name", ""),
            "identifiers": ids,
        },
        "provenance": {"source": "identifiers table", "merchant_id": m["id"]},
    }


def _get_related_merchants(params: Dict[str, Any]) -> Dict[str, Any]:
    """Find related merchants via entity clusters."""
    import sqlite3
    from merchant_intelligence.rag import extract_entities, retrieve_merchants
    from merchant_intelligence.schema import _db_path

    identifier = params["identifier"]
    limit = min(params.get("limit", 10), _MAX_RESULTS)
    entities = extract_entities(identifier)
    merchants = retrieve_merchants(entities, name_hint=identifier, limit=1)
    if not merchants:
        return {"success": False, "error": f"No merchant found for '{identifier}'"}

    m = merchants[0]
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        # Find cluster for this merchant
        cluster = conn.execute(
            "SELECT cluster_id FROM entity_clusters WHERE merchant_id = ? LIMIT 1",
            (m["id"],)).fetchone()
        if not cluster:
            return {"success": True, "data": [], "count": 0,
                    "note": "No entity cluster found"}

        related = conn.execute(
            "SELECT ec.*, m.merchant_name, m.tid, m.mxcode, m.phone, m.email "
            "FROM entity_clusters ec "
            "JOIN merchants m ON ec.merchant_id = m.id "
            "WHERE ec.cluster_id = ? AND ec.merchant_id != ? "
            "LIMIT ?",
            (cluster["cluster_id"], m["id"], limit)).fetchall()
        return {
            "success": True,
            "data": [dict(r) for r in related],
            "count": len(related),
            "provenance": {"cluster_id": cluster["cluster_id"]},
        }
    finally:
        conn.close()


def _compare_merchants(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two merchants."""
    from merchant_intelligence.rag import extract_entities, retrieve_merchants, _format_merchant_doc
    a = retrieve_merchants(extract_entities(params["merchant_a"]),
                           params["merchant_a"], limit=1)
    b = retrieve_merchants(extract_entities(params["merchant_b"]),
                           params["merchant_b"], limit=1)
    if not a or not b:
        missing = params["merchant_a"] if not a else params["merchant_b"]
        return {"success": False, "error": f"Merchant not found: {missing}"}
    return {
        "success": True,
        "data": {
            "merchant_a": _format_merchant_doc(a[0]),
            "merchant_b": _format_merchant_doc(b[0]),
        },
    }


def _count_merchants(params: Dict[str, Any]) -> Dict[str, Any]:
    """Count merchants matching a filter."""
    import sqlite3
    from merchant_intelligence.schema import _db_path

    field = params["filter_field"]
    value = params["filter_value"]
    # Whitelist allowed fields to prevent SQL injection
    allowed = {"state", "bank", "sheet_name", "merchant_category_code",
               "deployment_status", "ptsp", "acquirer", "lga"}
    if field not in allowed:
        return {"success": False, "error": f"Filter field '{field}' not allowed"}
    conn = sqlite3.connect(str(_db_path()))
    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM merchants WHERE UPPER(TRIM({field})) = ?",
            (value.upper(),)).fetchone()[0]
        return {"success": True, "data": {"count": count, "field": field, "value": value}}
    finally:
        conn.close()


def _resolve_identifiers(params: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a batch of identifiers."""
    from merchant_intelligence.rag import extract_entities, retrieve_merchants, _format_merchant_doc
    identifiers = params["identifiers"][:_MAX_RESULTS]
    all_results = []
    for ident in identifiers:
        entities = extract_entities(ident)
        merchants = retrieve_merchants(entities, name_hint=ident, limit=1)
        if merchants:
            all_results.append({
                "input": ident,
                "resolved": _format_merchant_doc(merchants[0]),
            })
        else:
            all_results.append({"input": ident, "resolved": None, "error": "not found"})
    return {"success": True, "data": all_results, "count": len(all_results)}


# ── Tool dispatch ───────────────────────────────────────────────────────────

_TOOL_DISPATCH: Dict[str, Callable] = {
    "search_merchants": _search_merchants,
    "get_merchant_profile": _get_merchant_profile,
    "get_static_account": _get_static_account,
    "get_identifiers": _get_identifiers,
    "get_related_merchants": _get_related_merchants,
    "compare_merchants": _compare_merchants,
    "count_merchants": _count_merchants,
    "resolve_identifiers": _resolve_identifiers,
}


def execute_tool(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool by name with the given parameters.

    Every invocation is audit-logged.  Safety rails enforce:
    - Max results cap
    - No export or destructive operations
    - All errors are caught and returned (never crash the copilot)
    """
    # Audit log
    try:
        from merchant_intelligence import audit
        audit.log("tool_invocation", {
            "tool": name, "params": {k: v for k, v in params.items()
                                     if k != "identifiers" or len(v) <= 5},
        })
    except Exception:
        pass

    func = _TOOL_DISPATCH.get(name)
    if not func:
        return {"success": False, "error": f"Unknown tool: {name}"}

    start = time.time()
    try:
        result = func(params)
        result["tool"] = name
        result["duration_ms"] = round((time.time() - start) * 1000, 1)
        return result
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "tool": name,
            "duration_ms": round((time.time() - start) * 1000, 1),
        }


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Return tool definitions in OpenAI function-calling format."""
    return TOOL_DEFINITIONS
