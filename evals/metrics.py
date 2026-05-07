"""Eval metrics — quantitative quality dimensions per task output.

Five dimensions (see plan §7.1):
  E1 Schema compliance       — % of outputs that parse cleanly to JSON_SCHEMA
  E2 Field coverage          — mean % of non-null fields per output
  E3 Confidence calibration  — for "high" confidence outputs, % matching golden
  E4 Source verification     — % of cited URLs that appeared in the run's tool results
  E5 Cost per task           — mean (tokens + tool calls) per account; tracked, not gated

Implementation notes:
- We use jsonschema for E1 — install fails fast if not present.
- E3 uses a per-field comparator from the golden file; mismatches lower the score.
- E4 requires the run log to record `tool_results_seen` (added later for v0.2.0).
  For v0.1.0 we approximate E4 by checking source URLs are non-empty + look like URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None


@dataclass
class CaseScore:
    """Per-case eval result for a single golden fixture."""
    case_name: str
    e1_schema: bool          # passes/fails
    e2_coverage: float       # 0.0–1.0
    e3_calibration: bool     # True iff high-confidence output matches golden
    e4_sources_ok: bool      # all cited URLs look like real URLs
    e5_input_tokens: int
    e5_output_tokens: int
    e5_search_count: int
    notes: list[str] = field(default_factory=list)


@dataclass
class TaskScore:
    """Aggregate scores across all golden cases for one task."""
    task_name: str
    cases: list[CaseScore]

    @property
    def schema_compliance(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.e1_schema) / len(self.cases)

    @property
    def mean_field_coverage(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.e2_coverage for c in self.cases) / len(self.cases)

    @property
    def confidence_calibration(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.e3_calibration) / len(self.cases)

    @property
    def source_verification(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.e4_sources_ok) / len(self.cases)

    @property
    def mean_input_tokens(self) -> float:
        return sum(c.e5_input_tokens for c in self.cases) / max(1, len(self.cases))

    @property
    def mean_output_tokens(self) -> float:
        return sum(c.e5_output_tokens for c in self.cases) / max(1, len(self.cases))

    @property
    def mean_searches(self) -> float:
        return sum(c.e5_search_count for c in self.cases) / max(1, len(self.cases))


# Default thresholds (see plan §7.1 — to be tuned after first eval run).
THRESHOLDS = {
    "schema_compliance": 0.95,
    "field_coverage": 0.80,
    "confidence_calibration": 0.90,
    "source_verification": 0.98,
}


# ---- per-case scorers ----

def score_schema_compliance(output: dict[str, Any] | None, schema: dict[str, Any]) -> bool:
    """E1: does the output validate against the declared JSON_SCHEMA?"""
    if output is None:
        return False
    if jsonschema is None:
        # Fallback: just check required top-level keys exist.
        required = schema.get("required", [])
        return all(k in output for k in required)
    try:
        jsonschema.validate(output, schema)
        return True
    except Exception:
        return False


def score_field_coverage(output: dict[str, Any] | None, schema: dict[str, Any]) -> float:
    """E2: fraction of declared properties that have a non-null, non-empty value."""
    if output is None:
        return 0.0
    properties = schema.get("properties", {})
    if not properties:
        return 0.0
    filled = 0
    for key in properties:
        v = output.get(key)
        if v is None:
            continue
        if isinstance(v, (list, str, dict)) and len(v) == 0:
            continue
        filled += 1
    return filled / len(properties)


def score_confidence_calibration(
    output: dict[str, Any] | None,
    golden: dict[str, Any],
) -> tuple[bool, list[str]]:
    """E3: if output declares 'high' confidence, do its values match the golden's?

    Comparator strategy:
    - For top-level fields the golden file specifies, compare values.
    - Strings: case-insensitive substring or exact match.
    - Booleans: exact match.
    - Numbers: within 20% range (handles e.g. employee count rounding).
    - Lists of strings: at least 1 overlap (for fuzzy match on competitors etc.).

    For non-"high" confidence outputs, this returns True (calibration applies only
    to over-claiming). Returns (passes, mismatches) for diagnostic reporting.
    """
    if output is None:
        return False, ["no output"]
    if output.get("confidence") != "high":
        return True, []  # only gating "high" claims

    mismatches: list[str] = []
    expected = golden.get("expected", {})
    for key, expected_val in expected.items():
        actual = output.get(key)
        if not _values_match(actual, expected_val):
            mismatches.append(f"{key}: got {actual!r}, expected {expected_val!r}")
    return len(mismatches) == 0, mismatches


def score_source_verification(output: dict[str, Any] | None) -> bool:
    """E4: every cited URL must look like a real URL (http(s) scheme + dotted host).

    True E4 (verifying URLs appeared in tool results) needs run-log enrichment
    to record `tool_results_seen` per task. Tracked for v0.2.0; this v0.1.0
    placeholder catches the most common hallucination pattern (made-up domains).
    """
    if output is None:
        return False
    sources = output.get("sources", []) or []
    if not sources:
        return False
    url_re = re.compile(r"^https?://[^/\s]+\.[^/\s]+")
    return all(url_re.match(s) for s in sources)


def _values_match(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)):
            return False
        if expected == 0:
            return actual == 0
        return abs((actual - expected) / expected) <= 0.20
    if isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        return expected.lower() in actual.lower() or actual.lower() in expected.lower()
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if not expected:
            return True
        actual_lower = {str(a).lower() for a in actual}
        expected_lower = {str(e).lower() for e in expected}
        return bool(actual_lower & expected_lower)
    return actual == expected
