"""telemetry.py — Lightweight structured tracing for the merchant intelligence API.

Provides:
  - Request-level tracing (method, path, status, duration)
  - Intent execution tracing (intent, pipeline, duration, identifiers count)
  - DB query tracing (query tag, duration, row count)
  - Structured JSON log lines to ``data/telemetry.jsonl``
  - Optional OpenTelemetry SDK integration when ``opentelemetry`` is installed

Usage::

    from merchant_intelligence.telemetry import trace_request, trace_intent, trace_db

    # In middleware:
    with trace_request("POST", "/api/task") as span:
        span.set("status", 200)

    # In pipeline execution:
    with trace_intent("static_account", n_identifiers=3) as span:
        ...

    # In DB helpers:
    with trace_db("resolve_any", n_rows=5) as span:
        ...

When OpenTelemetry is installed and configured, these context managers also
create real OTel spans.  When it's not, they just write structured log lines.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TELEMETRY_LOG = _PROJECT_ROOT / "data" / "telemetry.jsonl"

# ── try importing OpenTelemetry (optional) ──────────────────────────────────
_otel_tracer = None
try:
    from opentelemetry import trace as _otel_trace
    _otel_tracer = _otel_trace.get_tracer("merchant-intelligence")
except ImportError:
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append_log(record: Dict[str, Any]) -> None:
    """Append a structured log line to the telemetry JSONL file."""
    _TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_TELEMETRY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass  # never break the app for telemetry logging


# ── request tracing ─────────────────────────────────────────────────────────
@dataclass
class RequestSpan:
    method: str
    path: str
    start: float = field(default_factory=time.time)
    status: int = 200
    duration_ms: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def finish(self) -> Dict[str, Any]:
        self.duration_ms = round((time.time() - self.start) * 1000, 1)
        record = {
            "type": "request",
            "ts": _now_iso(),
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }
        if self.extras:
            record.update(self.extras)
        _append_log(record)
        return record


@contextmanager
def trace_request(method: str, path: str):
    """Context manager that traces an HTTP request."""
    span = RequestSpan(method, path)
    otel_span = None
    if _otel_tracer:
        otel_span = _otel_tracer.start_span(f"{method} {path}")
    try:
        yield span
    except Exception as exc:
        span.status = 500
        span.set("error", str(exc))
        if otel_span:
            otel_span.record_exception(exc)
        raise
    finally:
        span.finish()
        if otel_span:
            otel_span.set_status(otel_trace.StatusCode.OK if span.status < 400
                                 else otel_trace.StatusCode.ERROR)
            otel_span.end()


# ── intent execution tracing ────────────────────────────────────────────────
@dataclass
class IntentSpan:
    intent: str
    start: float = field(default_factory=time.time)
    n_identifiers: int = 0
    n_rows: int = 0
    success: bool = True
    error: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def finish(self) -> Dict[str, Any]:
        duration_ms = round((time.time() - self.start) * 1000, 1)
        record = {
            "type": "intent",
            "ts": _now_iso(),
            "intent": self.intent,
            "duration_ms": duration_ms,
            "n_identifiers": self.n_identifiers,
            "n_rows": self.n_rows,
            "success": self.success,
        }
        if self.error:
            record["error"] = self.error
        if self.extras:
            record.update(self.extras)
        _append_log(record)
        return record


@contextmanager
def trace_intent(intent: str, n_identifiers: int = 0):
    """Context manager that traces an intent pipeline execution."""
    span = IntentSpan(intent=intent, n_identifiers=n_identifiers)
    otel_span = None
    if _otel_tracer:
        otel_span = _otel_tracer.start_span(f"intent:{intent}")
    try:
        yield span
    except Exception as exc:
        span.success = False
        span.error = str(exc)
        if otel_span:
            otel_span.record_exception(exc)
        raise
    finally:
        span.finish()
        if otel_span:
            otel_span.end()


# ── DB query tracing ────────────────────────────────────────────────────────
@dataclass
class DBSpan:
    query_tag: str
    start: float = field(default_factory=time.time)
    n_rows: int = 0
    duration_ms: float = 0.0

    def finish(self) -> Dict[str, Any]:
        self.duration_ms = round((time.time() - self.start) * 1000, 1)
        record = {
            "type": "db",
            "ts": _now_iso(),
            "query": self.query_tag,
            "duration_ms": self.duration_ms,
            "n_rows": self.n_rows,
        }
        _append_log(record)
        return record


@contextmanager
def trace_db(query_tag: str, n_rows: int = 0):
    """Context manager that traces a DB query."""
    span = DBSpan(query_tag=query_tag)
    try:
        yield span
    finally:
        span.n_rows = n_rows
        span.finish()


# ── telemetry history (for the admin dashboard) ─────────────────────────────
def recent_telemetry(n: int = 50, trace_type: Optional[str] = None) -> list:
    """Read the last N telemetry records, optionally filtered by type."""
    if not _TELEMETRY_LOG.exists():
        return []
    lines = _TELEMETRY_LOG.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-n * 3:]:  # read extra in case of filtering
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if trace_type and rec.get("type") != trace_type:
                continue
            records.append(rec)
        except json.JSONDecodeError:
            pass
        if len(records) >= n:
            break
    return records


def telemetry_stats() -> Dict[str, Any]:
    """Aggregate telemetry stats for the admin dashboard."""
    records = recent_telemetry(n=500)
    if not records:
        return {"total": 0, "requests": 0, "intents": 0, "db_queries": 0}

    requests = [r for r in records if r.get("type") == "request"]
    intents = [r for r in records if r.get("type") == "intent"]
    db_queries = [r for r in records if r.get("type") == "db"]

    def _avg(items, key):
        vals = [r.get(key, 0) for r in items if r.get(key)]
        return round(sum(vals) / len(vals), 1) if vals else 0

    intent_counts: Dict[str, int] = {}
    for r in intents:
        intent_counts[r.get("intent", "?")] = intent_counts.get(r.get("intent", "?"), 0) + 1

    return {
        "total": len(records),
        "requests": len(requests),
        "intents": len(intents),
        "db_queries": len(db_queries),
        "avg_request_ms": _avg(requests, "duration_ms"),
        "avg_intent_ms": _avg(intents, "duration_ms"),
        "avg_db_ms": _avg(db_queries, "duration_ms"),
        "intent_breakdown": dict(sorted(intent_counts.items(), key=lambda x: -x[1])[:10]),
    }
