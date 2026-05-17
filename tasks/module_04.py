"""Module 4 — Corporate structure. Output → sister/child (sibling/child brands)
+ Parent (parent company name; self-name if itself a parent; empty if standalone)."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_04_corporate_structure as prompt
from tasks.base import DetectedEvent, Task


class Module04CorporateStructure(Task):
    name = "module_04_corporate_structure"
    section = "Overview"   # contributes a small note inside the Overview block-set
    subsection = None
    prompt_module = prompt
    synthesis_only = True   # reads ResearchPass output, no own tools
    model_tier = "fast"     # 2026-05-12 cost-cutting: standalone/subsidiary/parent
                            # classification is shallow pattern-matching, Haiku handles

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        # Parent: parent name | own name (if itself the parent) | empty.
        structure = output.get("structure_type")
        parent_name = output.get("parent_company")
        # `_account_name` is injected by Task.run() from the Notion record so
        # parent-type companies get their canonical CRM name (not whatever the
        # model chose to call them).
        company_name = output.get("_account_name")
        siblings = output.get("notable_sister_or_child_brands") or []

        if structure == "subsidiary" and parent_name:
            fields[crm.PROP_PARENT] = _rich_text(parent_name)
        elif company_name and (structure == "parent" or siblings):
            # Self-name guard for standalone-parents like AlphaSense: the model
            # often classifies a company as `standalone` even when it owns
            # notable child brands (e.g. AlphaSense + Tegus). Treat the
            # presence of any sister/child brand as evidence the company is
            # itself a parent and write its own name in `Parent`.
            fields[crm.PROP_PARENT] = _rich_text(company_name)
        # standalone with no children → leave empty (preserves manual edits)

        # sister/child: capture sister/child brands (if any), comma-separated.
        if siblings:
            fields[crm.PROP_SISTER_CHILD] = _rich_text(", ".join(siblings))

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


    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list[DetectedEvent]:
        """Alert on a corporate-structure transition. The two cases that
        meaningfully change a BDR's prospecting:

        - standalone → subsidiary (the company was acquired)
        - standalone → parent  (the company acquired someone)
        - subsidiary → standalone (spin-off — sometimes a buying-friendly state)
        - parent → subsidiary (the parent itself got acquired)
        """
        if prev_output is None or curr_output is None:
            return []
        prev_type = prev_output.get("structure_type")
        curr_type = curr_output.get("structure_type")
        if not curr_type or prev_type == curr_type:
            return []
        # First-detection guard: if prev_type is missing entirely (None / not
        # in the output schema) treat as no-prior-comparable-state.
        if not prev_type:
            return []
        new_parent = curr_output.get("parent_company") or ""
        summary = (
            f"Corporate structure changed: {prev_type} → {curr_type}"
            + (f" (parent: {new_parent})" if new_parent else "")
        )
        signature = f"module_05:structure_type:{prev_type}_to_{curr_type}"
        return [DetectedEvent(
            account_page_id="",
            module=self.name,
            signal_type="M&A",
            summary=summary,
            source_url=None,
            signature=signature,
        )]


def _rich_text(s: str) -> dict[str, Any]:
    """Notion text property update payload."""
    return {"rich_text": [{"type": "text", "text": {"content": s}}]}
