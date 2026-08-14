"""
tasks — Natural-language task engine (package).

Turns a pasted block into an executable pipeline against intelligence.db.
Split from the original single tasks.py into parser / intents / db /
pipelines / engine for maintainability. This __init__ re-exports the full
public surface so every existing import keeps working:

    from merchant_intelligence import tasks
    from merchant_intelligence.tasks import detect_task, execute_task, ...
"""
from .engine import (
    analyze, detect_task, execute_task, inherit_reference, last_entities,
    remember_entities, suggest_clarification, suggest_next_steps, top_two_gap,
)
from .intents import (
    build_execution_plan, detect_intent, detect_intents,
    extract_clause_entities,
)
from .models import PipelineResult, TaskDescriptor
from .parser import (
    extract_compare_pair, extract_names, extract_params, extract_reference,
    extract_segment, looks_like_address, parse_identifiers,
    parse_named_identifiers, split_clauses,
)
from .vocab import COMPILED_INTENT_PATTERNS, MAX_INPUT_CHARS, _whole_word_re

# Constants re-exported for back-compat (tests / scripts reach into the
# module, e.g. tests/test_tasks.py imports MAX_INPUT_CHARS / _whole_word_re).
from .vocab import (  # noqa: E402  (grouped back-compat re-export)
    ADDRESS_LOCALITY_WORDS,
    ADDRESS_SOURCE_PRIORITY,
    ADDRESS_TYPE_WORDS,
    CHAINABLE,
    ID_KINDS,
    INSTRUCTION_WORDS,
    INTENT_KEYWORDS,
    INTENT_PATTERNS,
    LIGHT_NAME_STOPS,
    MAX_RESULT_LIMIT,
    NAME_ANCHORS,
    NAME_CAPABLE_INTENTS,
    NAME_STOP_WORDS,
    NIGERIA_STATES,
    PRESENCE_PATTERNS,
    SAFE_SHORT_STATES,
    SEGMENT_COLLECTIVE,
    SEGMENT_EXTRA_STOP,
    SEGMENT_FIELDS,
    SEGMENT_STOP_WORDS,
    _lower,
)

__all__ = [
    "analyze", "build_execution_plan", "detect_intent", "detect_intents",
    "detect_task", "execute_task", "extract_clause_entities",
    "extract_compare_pair", "extract_names", "extract_params",
    "extract_segment", "looks_like_address", "parse_identifiers",
    "parse_named_identifiers",
    "split_clauses", "suggest_clarification", "suggest_next_steps",
    "top_two_gap",
    "PipelineResult", "TaskDescriptor",
    "COMPILED_INTENT_PATTERNS", "MAX_INPUT_CHARS", "MAX_RESULT_LIMIT",
    "INTENT_PATTERNS", "INTENT_KEYWORDS", "ID_KINDS", "CHAINABLE",
    "ADDRESS_TYPE_WORDS", "ADDRESS_LOCALITY_WORDS", "ADDRESS_SOURCE_PRIORITY",
    "INSTRUCTION_WORDS", "NAME_CAPABLE_INTENTS", "NAME_STOP_WORDS",
    "NIGERIA_STATES", "PRESENCE_PATTERNS", "SAFE_SHORT_STATES",
    "SEGMENT_FIELDS", "SEGMENT_COLLECTIVE", "SEGMENT_STOP_WORDS",
    "SEGMENT_EXTRA_STOP", "NAME_ANCHORS", "LIGHT_NAME_STOPS", "_lower",
    "_whole_word_re",
]
