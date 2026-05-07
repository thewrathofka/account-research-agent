"""Eval runner — runs a task against the golden set, prints/persists scores.

Usage:
    python -m evals.runner --task module_01_gate
    python -m evals.runner --task module_01_gate --provider openai
    python -m evals.runner --all                     # every task with a golden file
    python -m evals.runner --task module_01_gate --offline   # skip API calls

Output:
    Per-case PASS/FAIL summary
    Aggregate E1-E5 metrics
    Comparison vs THRESHOLDS — flags any regression
    Markdown summary file at evals/results/<YYYY-MM-DD>-<task>-<version>.md

Golden file format (one per case, in evals/golden/<slug>.json):
{
  "company_name": "Stripe, Inc.",
  "expected": {
    "size_band": "5000+",
    "operates_in_eu_or_na": true,
    ...
  },
  "notes": "Free-form annotation explaining edge cases / why expected values."
}

A single golden file CAN cover multiple tasks per company by adding
top-level keys named like the task: {"module_01_gate": {"expected": {...}}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from evals.metrics import (
    THRESHOLDS, CaseScore, TaskScore,
    score_confidence_calibration, score_field_coverage,
    score_schema_compliance, score_source_verification,
)
from providers import get_provider
from tasks import TASK_REGISTRY


GOLDEN_DIR = Path(__file__).parent / "golden"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class GoldenCase:
    slug: str                          # filename stem
    company_name: str
    expected_per_task: dict[str, dict[str, Any]]   # task_name -> {expected: {...}}
    notes: str = ""


def load_golden_cases() -> list[GoldenCase]:
    """Read every JSON file in evals/golden/ as a GoldenCase."""
    cases = []
    if not GOLDEN_DIR.exists():
        return cases
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        # Two formats accepted: top-level "expected" (single task) or task-keyed.
        company = data.get("company_name", path.stem)
        notes = data.get("notes", "")
        if "expected" in data:
            # Single-task format — accept as a wildcard match.
            expected = {"*": {"expected": data["expected"]}}
        else:
            expected = {
                k: v for k, v in data.items()
                if k.startswith("module_") or k == "company_overview"
            }
        cases.append(GoldenCase(slug=path.stem, company_name=company,
                                expected_per_task=expected, notes=notes))
    return cases


def get_task_schema(task_name: str) -> dict[str, Any]:
    """Find JSON_SCHEMA in the prompt module for this task."""
    task_cls = TASK_REGISTRY.get(task_name)
    if task_cls is None:
        raise ValueError(f"Unknown task: {task_name}")
    prompt_module = task_cls.prompt_module
    return getattr(prompt_module, "JSON_SCHEMA", {})


def run_eval(task_name: str, provider_name: str | None = None,
             offline: bool = False) -> TaskScore:
    """Run task_name against every applicable golden case, return aggregated TaskScore."""
    if task_name not in TASK_REGISTRY:
        raise ValueError(f"Unknown task: {task_name}. Known: {list(TASK_REGISTRY)}")

    schema = get_task_schema(task_name)
    cases = load_golden_cases()
    if not cases:
        print("No golden cases found in evals/golden/. Add .json files there.")
        return TaskScore(task_name=task_name, cases=[])

    if not offline:
        provider = get_provider(provider_name)
    else:
        provider = None

    case_scores: list[CaseScore] = []
    for gc in cases:
        # Match: either task_name has a key in this golden, or "*" wildcard exists.
        golden = gc.expected_per_task.get(task_name) or gc.expected_per_task.get("*")
        if not golden:
            continue

        if offline:
            # Synthesize an output by reading from a sidecar file evals/golden/<slug>.<task>.actual.json
            # if present. Useful for replaying a previous live run without re-charging.
            actual_path = GOLDEN_DIR / f"{gc.slug}.{task_name}.actual.json"
            output = json.loads(actual_path.read_text()) if actual_path.exists() else None
            input_tokens = output_tokens = search_count = 0
        else:
            task = TASK_REGISTRY[task_name]()
            print(f"  Running {task_name} on {gc.company_name}…", flush=True)
            result = task.run(gc.company_name, provider=provider)
            output = result.output
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            search_count = result.search_count
            # Persist for offline replay.
            if output is not None:
                actual_path = GOLDEN_DIR / f"{gc.slug}.{task_name}.actual.json"
                actual_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

        e1 = score_schema_compliance(output, schema)
        e2 = score_field_coverage(output, schema)
        e3, mismatches = score_confidence_calibration(output, golden)
        e4 = score_source_verification(output)

        notes = []
        if not e1:
            notes.append("E1 fail: schema validation failed")
        if mismatches:
            notes.append("E3 mismatches: " + "; ".join(mismatches[:3]))
        if not e4:
            notes.append("E4 fail: missing or malformed source URLs")

        case_scores.append(CaseScore(
            case_name=gc.slug, e1_schema=e1, e2_coverage=e2,
            e3_calibration=e3, e4_sources_ok=e4,
            e5_input_tokens=input_tokens, e5_output_tokens=output_tokens,
            e5_search_count=search_count, notes=notes,
        ))

    return TaskScore(task_name=task_name, cases=case_scores)


def print_score(score: TaskScore, provider_name: str) -> None:
    """Pretty-print a TaskScore. Highlights threshold violations."""
    print(f"\n=== {score.task_name} (provider: {provider_name}) ===")
    print(f"Cases evaluated: {len(score.cases)}")
    if not score.cases:
        return

    # Per-case lines
    for c in score.cases:
        flags = []
        if not c.e1_schema:
            flags.append("E1")
        if c.e2_coverage < THRESHOLDS["field_coverage"]:
            flags.append(f"E2={c.e2_coverage:.0%}")
        if not c.e3_calibration:
            flags.append("E3")
        if not c.e4_sources_ok:
            flags.append("E4")
        status = "PASS" if not flags else f"FAIL ({','.join(flags)})"
        print(f"  {c.case_name:20s} {status}")
        for n in c.notes:
            print(f"    {n}")

    # Aggregate
    print()
    metrics = [
        ("Schema compliance",    score.schema_compliance,     THRESHOLDS["schema_compliance"]),
        ("Field coverage",       score.mean_field_coverage,   THRESHOLDS["field_coverage"]),
        ("Confidence calibration", score.confidence_calibration, THRESHOLDS["confidence_calibration"]),
        ("Source verification",  score.source_verification,   THRESHOLDS["source_verification"]),
    ]
    for label, value, threshold in metrics:
        passing = value >= threshold
        marker = "✓" if passing else "✗ BELOW THRESHOLD"
        print(f"  {label:25s} {value:.1%}  (threshold {threshold:.0%})  {marker}")

    print(f"\n  Mean cost/case: in={score.mean_input_tokens:,.0f} tok, "
          f"out={score.mean_output_tokens:,.0f} tok, "
          f"searches={score.mean_searches:.1f}")


def write_markdown_summary(score: TaskScore, provider_name: str,
                           prompt_version: str) -> Path:
    """Persist a human-readable run summary to evals/results/."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = RESULTS_DIR / f"{today}-{score.task_name}-{prompt_version}.md"
    lines = [
        f"# Eval — {score.task_name} ({prompt_version})",
        f"Provider: {provider_name}  ·  Date: {today}",
        f"Cases: {len(score.cases)}",
        "",
        "## Aggregate",
        f"- Schema compliance: {score.schema_compliance:.1%}",
        f"- Field coverage:    {score.mean_field_coverage:.1%}",
        f"- Confidence calibration: {score.confidence_calibration:.1%}",
        f"- Source verification: {score.source_verification:.1%}",
        f"- Mean tokens/case:  in={score.mean_input_tokens:,.0f}  out={score.mean_output_tokens:,.0f}",
        f"- Mean searches/case: {score.mean_searches:.1f}",
        "",
        "## Per-case",
    ]
    for c in score.cases:
        flags = []
        if not c.e1_schema:
            flags.append("E1")
        if c.e2_coverage < THRESHOLDS["field_coverage"]:
            flags.append("E2")
        if not c.e3_calibration:
            flags.append("E3")
        if not c.e4_sources_ok:
            flags.append("E4")
        verdict = "PASS" if not flags else f"FAIL ({','.join(flags)})"
        lines.append(f"- **{c.case_name}**: {verdict}")
        for n in c.notes:
            lines.append(f"  - {n}")
    path.write_text("\n".join(lines) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run evals for one or all Phase 1 tasks.")
    p.add_argument("--task", help="Task name (one of TASK_REGISTRY keys)")
    p.add_argument("--all", action="store_true", help="Run all Phase 1 tasks")
    p.add_argument("--provider", default=None,
                   help="Provider override (anthropic|openai|gemini)")
    p.add_argument("--offline", action="store_true",
                   help="Don't call APIs; replay sidecar .actual.json files instead")
    args = p.parse_args(argv)

    if not args.task and not args.all:
        p.error("provide --task NAME or --all")

    from tasks import PHASE1_TASKS
    tasks = PHASE1_TASKS if args.all else [args.task]
    provider_name = args.provider or "offline" if args.offline else (args.provider or "anthropic")

    for tn in tasks:
        if tn not in TASK_REGISTRY:
            print(f"WARN: skipping unknown task {tn}")
            continue
        score = run_eval(tn, provider_name=args.provider, offline=args.offline)
        print_score(score, provider_name=provider_name)
        prompt_module = TASK_REGISTRY[tn].prompt_module
        prompt_version = getattr(prompt_module, "VERSION", "unknown")
        out_path = write_markdown_summary(score, provider_name, prompt_version)
        print(f"  Summary written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
