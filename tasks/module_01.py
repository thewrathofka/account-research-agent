"""Module 1 — Size + EU/NA gate task.

Behaviour (see plan §5 step 4):
- Verifies employee count + EU/NA presence.
- If gate fails (no EU/NA), the orchestrator sets Research Status=out_of_scope,
  writes the reason to the page body, and SKIPS all downstream tasks for this
  account. Implementation of skip-downstream lives in orchestrator._process_account.

Output mapping:
- The model returns an integer `employee_count_estimate`. We deterministically
  bucket that integer into the existing Notion `Size` select via `size_bucket()`.
  This is intentionally simpler than asking the model to pick a bucket directly:
  picking a number is a more reliable model task than mapping nuance to the
  right one of four labels, and any bucket-edge ambiguity is now resolved in
  one obvious place in code instead of inside the prompt.

  We do NOT write a separate Employee Count number property — the Money Moguls
  CRM only carries the Size select. The integer lives in the run log
  (output_json) for audit; only the bucket lands in Notion.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_01_gate as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


def size_bucket(employee_count: int | None) -> str | None:
    """Map a raw employee count to the Notion Size select option.

    Buckets match the existing CRM vocab (SIZE_OPTIONS). Returns None for
    None/negative input rather than guessing — better to leave Size unset than
    write a wrong bucket.
    """
    if employee_count is None or employee_count < 0:
        return None
    if employee_count < 1000:
        return "<1000"
    if employee_count < 2000:
        return "1000-2000"
    if employee_count < 5000:
        return "2000-5000"
    return "5000+"


class Module01Gate(Task):
    name = "module_01_gate"
    section = "Overview"
    subsection = None
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        count = output.get("employee_count_estimate")
        if isinstance(count, int):
            bucket = size_bucket(count)
            if bucket in crm.SIZE_OPTIONS:
                fields[crm.PROP_SIZE] = {"select": {"name": bucket}}
        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.gate_passes(output):
            reason = output.get("reason_if_out_of_scope") or "No in-scope (EU/UK/Norway/Switzerland/NA) operations confirmed."
            return [crm.paragraph(f"Out of scope: {reason}")]

        emp = output.get("employee_count_estimate")
        bucket = size_bucket(emp) if isinstance(emp, int) else None
        emp_str = f"~{emp:,} employees" if isinstance(emp, int) else None
        size_str = bucket or "unknown size"

        regions = []
        if output.get("operates_in_eu"):
            regions.append("EU")
        if output.get("operates_in_uk"):
            regions.append("UK")
        if output.get("operates_in_norway"):
            regions.append("Norway")
        if output.get("operates_in_switzerland"):
            regions.append("Switzerland")
        if output.get("operates_in_na"):
            regions.append("NA")
        regions_str = " + ".join(regions) if regions else "no confirmed in-scope presence"

        parts = [f"Size band: {size_str}"]
        if emp_str:
            parts.append(emp_str)
        parts.append(f"Operations in {regions_str}")
        return [crm.paragraph(" · ".join(parts))]

    @staticmethod
    def gate_passes(output: dict[str, Any] | None) -> bool:
        """True iff the company should continue to downstream modules.

        Reads the v1.3.0 `operates_in_scope` field. Falls back to the v1.2.0
        `operates_in_eu_or_na` field for backward compatibility with cached
        outputs in run_log from prior batches.
        """
        if not output:
            return False
        if "operates_in_scope" in output:
            return bool(output.get("operates_in_scope"))
        return bool(output.get("operates_in_eu_or_na"))
