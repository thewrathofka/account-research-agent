"""Module 4 — Strategic narrative + pain tags (v2.0.0 redesign 2026-05-11).

Synthesis-only task that reads research_pass.raw_research plus three light
anchors (modules 1, 3, 12) and produces:
- A 1-2 paragraph narrative for the BDR analyst (positioning + growth + audience).
- A small set of pain tags (subset of crm.PAIN_POINT_TAG_OPTIONS) written to
  Notion's `Pain Point Tags` multi-select.

Drops the v1.x grounded-pain-hypotheses design entirely. Mechanical signal
modules (06, 07, 09, 10, 14) are NOT in scope here — they have their own
page-body sections and the Buying Signals subsections handle their narrative.
"""

from __future__ import annotations

import json
from typing import Any

import crm
import prompts.module_04_pain_points as prompt
from tasks.base import Task, _today_header


# Upstream modules whose structured outputs anchor the narrative without
# overwhelming it. research_pass.raw_research is the primary input;
# these are factual scaffolding.
ANCHOR_MODULES: list[str] = [
    "module_01_gate",
    "module_03_revenue_model",
    "module_12_competitor_snapshot",
]


class Module04PainPoints(Task):
    name = "module_04_pain_points"
    section = "Possible Pain Points"
    subsection = None
    prompt_module = prompt
    synthesis_only = True

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Fold raw_research + the three anchor module outputs into the
        synthesis prompt. Mechanical signal modules are intentionally absent
        — see module-level docstring."""
        research_pass = context.get("research_pass") or {}
        raw_research = research_pass.get("raw_research", "")
        research_sources = research_pass.get("sources", []) or []

        anchor_blocks: list[str] = []
        for mod_name in ANCHOR_MODULES:
            output = context.get(mod_name)
            if not output:
                anchor_blocks.append(f"### {mod_name}\n(not run or no output)\n")
                continue
            anchor_blocks.append(
                f"### {mod_name}\n```json\n"
                + json.dumps(output, indent=2, ensure_ascii=False)
                + "\n```\n"
            )
        anchors_text = "\n".join(anchor_blocks)

        # Union of upstream source URLs — model's `sources` field must be a
        # subset of these (Fix Appendix #6 — eval verifies subset).
        all_sources: list[str] = list(research_sources)
        seen: set[str] = set(all_sources)
        for mod_name in ANCHOR_MODULES:
            out = context.get(mod_name) or {}
            for url in out.get("sources", []) or []:
                if url and url not in seen:
                    seen.add(url)
                    all_sources.append(url)
        sources_block = "\n".join(f"- {u}" for u in all_sources) or "(no sources captured)"

        research_section = (
            f"## Research material (the narrative source)\n\n{raw_research}\n\n"
            if raw_research
            else "## Research material\n\n(no research_pass output available; "
                 "produce a short, low-confidence narrative based only on the "
                 "anchor modules below)\n\n"
        )

        return (
            f"{_today_header()}\n\n"
            f"Company: {account_name}.\n\n"
            f"{research_section}"
            f"## Factual anchors (structured outputs from prior modules)\n\n"
            f"{anchors_text}\n"
            f"## Available source URLs\n{sources_block}\n\n"
            "Write the analyst's brief per the schema. Narrative first — "
            "tell the strategic story of where this company is competitively "
            "and where they're trying to grow. Then pick 2-5 tags from the "
            "vocabulary that best describe the strategic pains your narrative "
            "names. No cold-email voice, no module-name citations, no "
            "horoscope filler."
        )

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        """Write the validated tags to Notion's `Pain Point Tags` multi-select.

        Unknown tags (anything outside crm.PAIN_POINT_TAG_OPTIONS) are dropped
        rather than raising — same pattern as module 7's BUYING_SIGNAL_OPTIONS
        guard. If the model returns zero valid tags we still emit an empty
        multi-select so a previous run's stale tags get cleared on rerun
        (same overwrite-on-write contract as Buying Signals).
        """
        tags = output.get("tags") or []
        valid = [t for t in tags if t in crm.PAIN_POINT_TAG_OPTIONS]
        return {
            crm.PROP_PAIN_POINT_TAGS: {
                "multi_select": [{"name": t} for t in valid],
            }
        }

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit narrative paragraphs + one tag bullet.

        The narrative may contain `[N]` citation markers as plain text — the
        orchestrator's per-section citation pass renumbers them and rewrites
        each marker into a clickable Notion link span pointing to the cited
        URL. The orchestrator also appends a single section-wide footnote
        bullet list at the end of `Possible Pain Points`, so this method does
        NOT emit footnotes itself (single source of truth).
        """
        narrative = (output.get("narrative") or "").strip()
        if not narrative:
            return [crm.paragraph(
                "No narrative produced — research material was insufficient "
                "to support a specific strategic story this run."
            )]

        blocks: list[dict[str, Any]] = []
        for para in [p.strip() for p in narrative.split("\n\n") if p.strip()]:
            blocks.append(crm.paragraph(para))

        tags = [t for t in (output.get("tags") or []) if t in crm.PAIN_POINT_TAG_OPTIONS]
        if tags:
            blocks.append(crm.bullet("Pain tags: " + " · ".join(tags)))

        return blocks
