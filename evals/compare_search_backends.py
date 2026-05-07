"""Side-by-side comparison of search backends (Tavily vs Brave vs Brave+Jina).

Runs module_01_gate against the existing golden cases for each backend, captures
E1-E4 metrics + cost (search count + token usage), and writes a comparison
markdown to evals/results/.

Usage:
    python -m evals.compare_search_backends

Reads BRAVE_API_KEY from .env. Skips backends whose API key is missing rather
than failing the whole run.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import config
from evals.metrics import (
    score_confidence_calibration, score_field_coverage,
    score_schema_compliance, score_source_verification,
)
from evals.runner import GOLDEN_DIR, RESULTS_DIR, load_golden_cases
from providers import get_provider
from tasks import TASK_REGISTRY


@dataclass
class BackendResult:
    backend: str
    case: str
    schema_ok: bool
    coverage: float
    calibration_ok: bool
    sources_ok: bool
    input_tokens: int
    output_tokens: int
    search_count: int
    duration_s: float
    error: str | None = None


def run_one(backend: str, task_name: str, case_name: str) -> BackendResult:
    """Run one task against one golden case under the given backend."""
    # Mutate the env-driven config for this run.
    config.SEARCH_BACKEND = backend

    task_cls = TASK_REGISTRY[task_name]
    task = task_cls()
    provider = get_provider()

    cases = load_golden_cases()
    case = next((c for c in cases if c.slug == case_name), None)
    if case is None:
        return BackendResult(backend, case_name, False, 0.0, False, False,
                             0, 0, 0, 0.0, error="golden case not found")

    schema = getattr(task_cls.prompt_module, "JSON_SCHEMA", {})
    golden_for_task = case.expected_per_task.get(task_name) or case.expected_per_task.get("*") or {}

    start = time.time()
    result = task.run(case.company_name, provider=provider)
    duration = time.time() - start

    output = result.output
    e1 = score_schema_compliance(output, schema)
    e2 = score_field_coverage(output, schema)
    e3, _ = score_confidence_calibration(output, golden_for_task)
    e4 = score_source_verification(output)

    return BackendResult(
        backend=backend, case=case_name,
        schema_ok=e1, coverage=e2, calibration_ok=e3, sources_ok=e4,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        search_count=result.search_count, duration_s=duration,
        error=result.error,
    )


def main() -> int:
    task_name = "module_01_gate"
    cases = ["stripe", "oracle", "razorpay"]

    backends_to_try = ["tavily"]
    if os.getenv("BRAVE_API_KEY") and "PLACEHOLDER" not in os.getenv("BRAVE_API_KEY", ""):
        backends_to_try.extend(["brave", "brave_jina"])
    else:
        print("Skipping Brave + Brave+Jina backends — BRAVE_API_KEY not in .env")

    print(f"Comparing search backends on {task_name} for cases: {cases}")
    print(f"Backends in this run: {backends_to_try}\n")

    rows: list[BackendResult] = []
    for backend in backends_to_try:
        print(f"--- Running backend: {backend} ---")
        for case in cases:
            print(f"  {case}…", end=" ", flush=True)
            r = run_one(backend, task_name, case)
            rows.append(r)
            verdict = "PASS" if (r.schema_ok and r.calibration_ok and r.sources_ok) else "FAIL"
            print(f"{verdict} (cov={r.coverage:.0%}, in={r.input_tokens:,}, srch={r.search_count})")

    # Aggregate per backend
    print("\n=== Summary ===")
    aggregates: dict[str, dict[str, Any]] = {}
    for backend in backends_to_try:
        backend_rows = [r for r in rows if r.backend == backend]
        agg = {
            "schema": sum(1 for r in backend_rows if r.schema_ok) / len(backend_rows),
            "coverage": sum(r.coverage for r in backend_rows) / len(backend_rows),
            "calibration": sum(1 for r in backend_rows if r.calibration_ok) / len(backend_rows),
            "sources": sum(1 for r in backend_rows if r.sources_ok) / len(backend_rows),
            "mean_input_tokens": sum(r.input_tokens for r in backend_rows) / len(backend_rows),
            "mean_search_count": sum(r.search_count for r in backend_rows) / len(backend_rows),
            "mean_duration_s": sum(r.duration_s for r in backend_rows) / len(backend_rows),
        }
        aggregates[backend] = agg
        print(f"  {backend:12s} schema={agg['schema']:.0%} cov={agg['coverage']:.0%} "
              f"calib={agg['calibration']:.0%} src={agg['sources']:.0%} | "
              f"in_tok={agg['mean_input_tokens']:,.0f} srch={agg['mean_search_count']:.1f} "
              f"dur={agg['mean_duration_s']:.1f}s")

    # Write markdown summary
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = RESULTS_DIR / f"{today}-search-backend-comparison.md"
    lines = [
        f"# Search backend comparison — {task_name} ({today})",
        "",
        f"Cases: {', '.join(cases)}",
        f"Provider: anthropic ({config.ANTHROPIC_MODELS['smart']})",
        "",
        "## Per-backend aggregates",
        "",
        "| Backend | Schema | Field Cov | Calibration | Sources | Mean tokens | Mean searches | Latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for backend, agg in aggregates.items():
        lines.append(
            f"| {backend} | {agg['schema']:.0%} | {agg['coverage']:.0%} | "
            f"{agg['calibration']:.0%} | {agg['sources']:.0%} | "
            f"{agg['mean_input_tokens']:,.0f} | {agg['mean_search_count']:.1f} | "
            f"{agg['mean_duration_s']:.1f}s |"
        )

    lines.extend(["", "## Per-case detail", ""])
    for r in rows:
        verdict = "PASS" if (r.schema_ok and r.calibration_ok and r.sources_ok) else "FAIL"
        lines.append(
            f"- **{r.backend}** / **{r.case}**: {verdict} "
            f"(cov={r.coverage:.0%}, in_tok={r.input_tokens:,}, srch={r.search_count}, dur={r.duration_s:.1f}s)"
        )
        if r.error:
            lines.append(f"  - ERROR: {r.error}")

    lines.extend([
        "",
        "## Decision criteria (from plan §1.5a step 8)",
        "",
        "Switch to Brave+Jina only if ALL three:",
        "1. Calibration matches Tavily within 5pp",
        "2. Total cost (search + tokens) drops by ≥30%",
        "3. Latency stays within 1.5× Tavily",
        "",
        "If criteria not met → keep Tavily (current default).",
    ])

    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nWritten: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
