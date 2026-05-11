"""Module 4 — Possible pain points (synthesis from modules 1, 3, 9, 10, 14).

Pure synthesis: no tools, single LLM call. Reads the structured JSON outputs
of the upstream modules from the context envelope and produces 2-4 pain
hypotheses, each grounded in specific cited data points.

Page-body section: `Possible Pain Points` (2nd top-level section after Overview).
No Notion property writes — body only.
"""

from __future__ import annotations

import json
from typing import Any

import crm
import prompts.module_04_pain_points as prompt
from tasks.base import Task, _today_header


# Upstream modules whose outputs ground the pain-point hypotheses. Order
# matches the spec (modules 1, 3, 9, 10, 14). The synthesis prompt expects
# these keys in the upstream block — if any are missing the model just
# notes thinner grounding and lowers confidence.
UPSTREAM_MODULES: list[str] = [
    "module_01_gate",
    "module_03_revenue_model",
    "module_07_trigger_events",   # buying signals inform AI-receptivity + campaign pains
    "module_09_creative_reality",
    "module_10_ad_library",
    "module_14_hiring_signal",
]


class Module04PainPoints(Task):
    name = "module_04_pain_points"
    section = "Possible Pain Points"
    subsection = None
    prompt_module = prompt
    synthesis_only = True

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Fold upstream structured outputs into the synthesis prompt.

        Note: research_pass raw_research is intentionally omitted — module 4's
        whole value is reasoning across already-distilled structured outputs,
        not re-reading raw research. Including it would burn input tokens
        and risk the model anchoring on raw text instead of citing module
        outputs (which is the grounding-evidence requirement).
        """
        upstream_blocks: list[str] = []
        for mod_name in UPSTREAM_MODULES:
            output = context.get(mod_name)
            if not output:
                upstream_blocks.append(f"### {mod_name}\n(not run or no output)\n")
                continue
            upstream_blocks.append(
                f"### {mod_name}\n```json\n"
                + json.dumps(output, indent=2, ensure_ascii=False)
                + "\n```\n"
            )

        upstream_text = "\n".join(upstream_blocks)

        # Sources from each upstream module — model's `sources` field must be
        # a subset of these (Fix Appendix #6 — eval verifies subset).
        all_sources: list[str] = []
        seen: set[str] = set()
        for mod_name in UPSTREAM_MODULES:
            out = context.get(mod_name) or {}
            for url in out.get("sources", []) or []:
                if url and url not in seen:
                    seen.add(url)
                    all_sources.append(url)
        sources_block = "\n".join(f"- {u}" for u in all_sources) or "(no sources captured)"

        return (
            f"{_today_header()}\n\n"
            f"Company: {account_name}.\n\n"
            "## Upstream module outputs\n\n"
            f"{upstream_text}\n"
            f"## Available source URLs\n{sources_block}\n\n"
            "Synthesize 2-4 grounded pain-point hypotheses per the schema. "
            "Each must cite specific data from the modules above — no generic "
            "horoscope pains. Drop a pain rather than ship one without "
            "grounding."
        )

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # page-body only

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        pains = output.get("pain_points") or []
        if not pains:
            return [crm.paragraph(
                "No specific pain hypotheses surfaced from upstream research "
                "with sufficient grounding."
            )]
        blocks: list[dict[str, Any]] = []
        for p in pains:
            ptype = p.get("pain_type", "?")
            hyp = p.get("hypothesis", "")
            angle = p.get("superside_angle", "")
            conf = p.get("confidence", "")
            grounding = p.get("grounding") or []

            # Lead line: pain_type · superside_angle · confidence + the hypothesis.
            head = f"{ptype} ({conf}) — Superside angle: {angle}. {hyp}"
            blocks.append(crm.bullet(head))

            # Each grounding bullet under the parent — but Notion's API treats
            # bullet children separately; for simplicity we emit them as nested
            # bullets at the same level prefixed with the citing module. The
            # page reader can still trace which datum supports which pain.
            for g in grounding:
                mod = g.get("module", "?")
                datum = g.get("datum", "")
                blocks.append(crm.bullet(f"    ↳ {mod}: {datum}"))

        return blocks
