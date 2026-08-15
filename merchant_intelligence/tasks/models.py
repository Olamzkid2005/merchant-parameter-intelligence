"""
models.py — Typed contracts for the task engine.

TaskDescriptor — the structured interpretation of a pasted request
                 (what detect_task produces)
PipelineResult — the render-ready result table (what execute_task returns)

Both are plain dataclasses with to_dict()/from_dict() so the public functions
keep returning plain dicts (JSON-serialisable for the API, .get()-friendly
for tests) while construction inside the engine is typed and typo-proof.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TaskDescriptor:
    """Structured interpretation of a pasted request.

    intent            primary intent (static_account, email, segment, ...)
    intents           every intent expressed in the request (compound runs
                      each of these pipelines in order)
    identifiers       identifier kind -> values (tid, mxcode, phone, email,
                      account, static, payable, bvn, mid, alias)
    named             pasted 'ID  NAME' pairs used for name cross-checking
    names             merchant names (name-only requests; [] when
                      identifiers drive the task)
    names_are_addresses  True when EVERY extracted name reads as an address
                      ('BRITISH INTERNATIONAL SCHOOL ROAD, LEKKI, LAGOS') —
                      such requests are matched against the address column,
                      never fuzzy name-searched
    key_merchants     key-merchant roots matched by the extracted names
                      (['MEDPLUS'] for 'medpluz emails') — enables the UI to
                      show WHY a request routed as a task
    identifier_count  total identifiers found
    has_instruction   instruction verbs present in the request
    multiline         request spans multiple lines
    confidence        0-100 heuristic confidence
    analysis          intent -> {score, confidence, matched} debug breakdown
    params            state / has[] / limit filters extracted from the text
    raw               the original request text
    llm_refined       True when the LLM interpreter adjusted the result
    segment           collection fragment ('NNPC') for segment-style intents
    segment_fields    requested columns ('address', 'email', ...)
    clauses           per-intent identifier attachments ('email for A and
                      phone for B' -> [{intent: email, identifiers: {…}},
                      {intent: phone, identifiers: {…}}]); [] when there is
                      nothing to attach
    excluded          intents the user negated ('...but not the change
                      history') — never scored, never run
    workflow          dependency-aware plan {workflow: [step...], steps:
                      [{intent, step, requires, resolved_internally,
                      produces}]} for the UI + debugging
    references_previous  True when the request points at a PREVIOUS request's
                      merchant ('get the tids for the above merchant') and
                      carries no identifier/name/segment of its own — the API
                      resolves it against the last remembered context
    """

    intent: str = "resolve"
    intents: List[str] = field(default_factory=list)
    identifiers: Dict[str, List[str]] = field(default_factory=dict)
    named: List[Dict[str, str]] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    names_are_addresses: bool = False
    key_merchants: List[str] = field(default_factory=list)
    identifier_count: int = 0
    has_instruction: bool = False
    multiline: bool = False
    confidence: int = 0
    analysis: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    is_task: bool = True
    llm_refined: bool = False
    segment: str = ""
    segment_fields: List[str] = field(default_factory=list)
    clauses: List[Dict[str, Any]] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)
    workflow: Dict[str, Any] = field(default_factory=dict)
    references_previous: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_task": self.is_task,
            "intent": self.intent,
            "intents": list(self.intents),
            "identifiers": {k: list(v) for k, v in self.identifiers.items()},
            "named": [dict(n) for n in self.named],
            "names": list(self.names),
            "names_are_addresses": self.names_are_addresses,
            "key_merchants": list(self.key_merchants),
            "identifier_count": self.identifier_count,
            "has_instruction": self.has_instruction,
            "multiline": self.multiline,
            "confidence": self.confidence,
            "analysis": self.analysis,
            "params": self.params,
            "raw": self.raw,
            "llm_refined": self.llm_refined,
            "segment": self.segment,
            "segment_fields": list(self.segment_fields),
            "clauses": [dict(c) for c in self.clauses],
            "excluded": list(self.excluded),
            "workflow": {
                "workflow": list(self.workflow.get("workflow", [])),
                "steps": [dict(s) for s in self.workflow.get("steps", [])],
            },
            "references_previous": self.references_previous,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDescriptor":
        return cls(
            intent=data.get("intent", "resolve"),
            intents=list(data.get("intents", [])),
            identifiers=dict(data.get("identifiers", {})),
            named=[dict(n) for n in data.get("named", [])],
            names=list(data.get("names", [])),
            names_are_addresses=data.get("names_are_addresses", False),
            key_merchants=list(data.get("key_merchants", [])),
            identifier_count=data.get("identifier_count", 0),
            has_instruction=data.get("has_instruction", False),
            multiline=data.get("multiline", False),
            confidence=data.get("confidence", 0),
            analysis=data.get("analysis", {}),
            params=data.get("params", {}),
            raw=data.get("raw", ""),
            is_task=data.get("is_task", True),
            llm_refined=data.get("llm_refined", False),
            segment=data.get("segment", ""),
            segment_fields=list(data.get("segment_fields", [])),
            clauses=[dict(c) for c in data.get("clauses", [])],
            excluded=list(data.get("excluded", [])),
            workflow=dict(data.get("workflow", {})),
            references_previous=data.get("references_previous", False),
        )


@dataclass
class PipelineResult:
    """Render-ready result table produced by execute_task (and pipelines).

    intent     primary intent that ran
    intents    the full compound intent list (execute_task only)
    pipeline   ordered step names, e.g. ['resolve_mx', 'static_account']
    summary    one-line human summary of what the pipeline found
    columns    the table column headers (frontend renders from these)
    rows       list of dicts whose keys match columns (one per result row)
    not_found  every input identifier/name that did not resolve, with a reason
    suggestions  one-click follow-up prompts (execute_task only)
    error      non-empty when the pipeline could not run (e.g. missing DB)
    workflow_executed  step verbs actually run, in dependency order (incl.
                  synthesized resolve steps) — the executed plan
    workflow_chain  step verb -> {"from": upstream steps, "values": n} for
                  every step that consumed upstream produced identifiers
    """

    intent: str = "resolve"
    intents: List[str] = field(default_factory=list)
    pipeline: List[str] = field(default_factory=list)
    summary: str = ""
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    not_found: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[Dict[str, str]] = field(default_factory=list)
    llm_refined: bool = False
    confidence: int = 0
    error: str = ""
    workflow_executed: List[str] = field(default_factory=list)
    workflow_chain: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "intents": list(self.intents),
            "pipeline": list(self.pipeline),
            "summary": self.summary,
            "columns": list(self.columns),
            "rows": [dict(r) for r in self.rows],
            "not_found": [dict(n) for n in self.not_found],
            "suggestions": [dict(s) for s in self.suggestions],
            "llm_refined": self.llm_refined,
            "confidence": self.confidence,
            "error": self.error,
            "workflow_executed": list(self.workflow_executed),
            "workflow_chain": dict(self.workflow_chain),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineResult":
        return cls(
            intent=data.get("intent", "resolve"),
            intents=list(data.get("intents", [])),
            pipeline=list(data.get("pipeline", [])),
            summary=data.get("summary", ""),
            columns=list(data.get("columns", [])),
            rows=list(data.get("rows", [])),
            not_found=list(data.get("not_found", [])),
            suggestions=list(data.get("suggestions", [])),
            llm_refined=data.get("llm_refined", False),
            confidence=data.get("confidence", 0),
            error=data.get("error", ""),
            workflow_executed=list(data.get("workflow_executed", [])),
            workflow_chain=dict(data.get("workflow_chain", {})),
        )
