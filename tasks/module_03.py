"""Module 3 — Strategic narrative + pain tags (v2.0.0 redesign 2026-05-11).

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
import prompts.module_03_pain_points as prompt
from tasks.base import DetectedEvent, Task, _today_header


# Pain Point Tags that signal a sales-relevant TIMING shift (not just narrative).
# When one of these newly appears in a rerun's tags, the BDR should look.
_TIMING_PAIN_TAGS: dict[str, str] = {
    "post-layoff overflow": "structure-ambiguous",
    "launch surge": "agency-switch",
}


# Upstream modules whose structured outputs anchor the narrative without
# overwhelming it. research_pass.raw_research is the primary input;
# these are factual scaffolding.
ANCHOR_MODULES: list[str] = [
    "module_01_gate",
    "module_02_revenue_model",
    "module_09_competitor_snapshot",
]


class Module03PainPoints(Task):
    name = "module_03_pain_points"
    section = "Possible Pain Points"
    subsection = None
    prompt_module = prompt
    synthesis_only = True
    model_tier = "fast"     # 2026-05-12 cost-cutting: narrative synthesis is the
                            # most reasoning-heavy of the fast-tier moves — re-
                            # evaluate after side-by-side comparison if Haiku
                            # output reads thin vs Sonnet on the same accounts

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

    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list[DetectedEvent]:
        """A new timing-shift Pain Point Tag (post-layoff overflow / launch
        surge) appearing where it wasn't before is sales-relevant — they
        indicate a moment when Superside's overflow capacity is most useful."""
        if prev_output is None or curr_output is None:
            return []
        prev_tags = set(prev_output.get("tags") or [])
        curr_tags = set(curr_output.get("tags") or [])
        events: list[DetectedEvent] = []
        for tag, signal_type in _TIMING_PAIN_TAGS.items():
            if tag in curr_tags and tag not in prev_tags:
                signature = f"module_03:tag:{tag}"
                events.append(DetectedEvent(
                    account_page_id="",
                    module=self.name,
                    signal_type=signal_type,
                    summary=f"Pain-point shift: '{tag}' newly identified in this run.",
                    source_url=None,
                    signature=signature,
                ))
        return events

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit one-line intro paragraph + per-pain-point bullets + tag bullet.

        v3.0.0 layout: each `pain_points[].label — body` becomes its own
        bulleted_list_item so the page-body section is scannable in 10
        seconds. `[N]` citation markers inside intro/body stay as plain
        text — the orchestrator's per-section citation pass renumbers them
        and rewrites each marker into a clickable Notion link span.

        Backwards-compatible degraded path: if a run cached a v2.x output
        with a `narrative` field instead of `pain_points`, render the
        narrative as paragraphs so old cache entries don't crash. New
        outputs (v3.0.0+) take the pain_points path.
        """
        intro = (output.get("intro") or "").strip()
        pain_points = output.get("pain_points") or []

        # v2.x fallback (cached old output): narrative string instead of intro+bullets.
        if not pain_points and (output.get("narrative") or "").strip():
            narrative = output["narrative"].strip()
            blocks: list[dict[str, Any]] = []
            for para in [p.strip() for p in narrative.split("\n\n") if p.strip()]:
                blocks.append(crm.paragraph(para))
            tags = [t for t in (output.get("tags") or []) if t in crm.PAIN_POINT_TAG_OPTIONS]
            if tags:
                blocks.append(crm.bullet("Pain tags: " + " · ".join(tags)))
            return blocks

        if not intro and not pain_points:
            return [crm.paragraph(
                "No pain points produced — research material was insufficient "
                "to support a specific strategic read this run."
            )]

        blocks = []
        if intro:
            blocks.append(crm.paragraph(intro))

        for pp in pain_points:
            label = (pp.get("label") or "").strip()
            body = (pp.get("body") or "").strip()
            if not label and not body:
                continue
            if label and body:
                text = f"{label} — {body}"
            else:
                text = label or body
            blocks.append(crm.bullet(text))

        tags = [t for t in (output.get("tags") or []) if t in crm.PAIN_POINT_TAG_OPTIONS]
        if tags:
            blocks.append(crm.bullet("Pain tags: " + " · ".join(tags)))

        return blocks
