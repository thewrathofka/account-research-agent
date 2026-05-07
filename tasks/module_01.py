"""Module 1 — Size + EU/NA gate task.

Behaviour (see plan §5 step 4):
- Verifies size band + EU/NA presence.
- If gate fails (no EU/NA), the orchestrator sets Research Status=out_of_scope,
  writes the reason to the page body, and SKIPS all downstream tasks for this
  account. Implementation of skip-downstream lives in orchestrator._process_account.

Output mapping:
- Notion property `Size` ← size_band (validated against the SIZE_OPTIONS vocab)
- Page body section "Overview" gets a short paragraph with the gate verdict
  + employee count estimate.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_01_gate as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class Module01Gate(Task):
    name = "module_01_gate"
    section = "Overview"
    subsection = None
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        size = output.get("size_band")
        if size in crm.SIZE_OPTIONS:
            fields[crm.PROP_SIZE] = {"select": {"name": size}}
        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.gate_passes(output):
            reason = output.get("reason_if_out_of_scope") or "No EU/NA operations confirmed."
            return [crm.paragraph(f"Out of scope: {reason}")]

        size = output.get("size_band") or "unknown size"
        emp = output.get("employee_count_estimate")
        emp_str = f"~{emp:,} employees" if emp else None

        regions = []
        if output.get("operates_in_eu"):
            regions.append("EU")
        if output.get("operates_in_na"):
            regions.append("NA")
        regions_str = " + ".join(regions) if regions else "no confirmed EU/NA presence"

        parts = [f"Size band: {size}"]
        if emp_str:
            parts.append(emp_str)
        parts.append(f"Operations in {regions_str}")
        return [crm.paragraph(" · ".join(parts))]

    @staticmethod
    def gate_passes(output: dict[str, Any] | None) -> bool:
        """True iff the company should continue to downstream modules."""
        if not output:
            return False
        return bool(output.get("operates_in_eu_or_na"))
