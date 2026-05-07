"""Module 5 — Corporate structure. Output → Parent-Child + Name of Parent properties."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_05_corporate_structure as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class Module05CorporateStructure(Task):
    name = "module_05_corporate_structure"
    section = "Overview"   # contributes a small note inside the Overview block-set
    subsection = None
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Inject gate output (size, regions) so the model doesn't re-search those facts."""
        gate = context.get("module_01_gate")
        if not gate:
            return f"Research the company: {account_name}"
        prior = []
        if gate.get("employee_count_estimate"):
            prior.append(f"~{gate['employee_count_estimate']:,} employees")
        if gate.get("size_band"):
            prior.append(f"size band {gate['size_band']}")
        regions = []
        if gate.get("operates_in_eu"):
            regions.append("EU")
        if gate.get("operates_in_na"):
            regions.append("NA")
        if regions:
            prior.append(f"operates in {' + '.join(regions)}")
        prior_str = "; ".join(prior) if prior else "(no prior context)"
        return (
            f"Research the company: {account_name}.\n"
            f"Prior research already established: {prior_str}. "
            f"You do NOT need to re-verify these facts — focus your searches on "
            f"corporate structure (parent / subsidiary / standalone, PE, sister brands)."
        )

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        # Name of Parent: parent name | own name (if itself the parent) | empty.
        structure = output.get("structure_type")
        parent_name = output.get("parent_company")
        company_name = output.get("company_name")

        if structure == "parent" and company_name:
            fields[crm.PROP_NAME_OF_PARENT] = _rich_text(company_name)
        elif structure == "subsidiary" and parent_name:
            fields[crm.PROP_NAME_OF_PARENT] = _rich_text(parent_name)
        # standalone → leave empty (don't write the property at all, preserves manual edits)

        # Parent-Child: capture sister/child brands (if any), comma-separated.
        siblings = output.get("notable_sister_or_child_brands") or []
        if siblings:
            fields[crm.PROP_PARENT_CHILD] = _rich_text(", ".join(siblings))

        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        structure = output.get("structure_type")
        parent = output.get("parent_company")
        pe_owner = output.get("pe_owner") if output.get("is_pe_owned") else None
        siblings = output.get("notable_sister_or_child_brands") or []

        if structure == "standalone":
            text = "Corporate structure: standalone."
        elif structure == "subsidiary" and parent:
            text = f"Corporate structure: subsidiary of {parent}."
        elif structure == "parent":
            text = "Corporate structure: parent company."
        else:
            text = "Corporate structure: unknown."

        if pe_owner:
            text += f" PE owner: {pe_owner}."
        if siblings:
            text += f" Notable brands: {', '.join(siblings)}."

        return [crm.paragraph(text)]


def _rich_text(s: str) -> dict[str, Any]:
    """Notion text property update payload."""
    return {"rich_text": [{"type": "text", "text": {"content": s}}]}
