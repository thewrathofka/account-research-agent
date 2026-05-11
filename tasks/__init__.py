"""Task registry. New tasks register their class here.

Phase 1 will add module_01 ... module_14. For v0.1.0 (post-refactor) we keep
the legacy CompanyOverview around so the smoke test still passes.
"""

from __future__ import annotations

from tasks._legacy import CompanyOverview
from tasks.base import Task, TaskResult
from tasks.module_01 import Module01Gate
from tasks.module_03 import Module03RevenueModel
from tasks.module_04 import Module04PainPoints
from tasks.module_05 import Module05CorporateStructure
from tasks.module_06 import Module06StructuralNews
from tasks.module_07 import Module07TriggerEvents
from tasks.module_09 import Module09CreativeReality
from tasks.module_10 import Module10AdLibrary
from tasks.module_12 import Module12CompetitorSnapshot
from tasks.module_13 import Module13IndustryPulse
from tasks.module_14 import Module14HiringSignal
from tasks.research_pass import ResearchPass


TASK_REGISTRY: dict[str, type[Task]] = {
    "company_overview": CompanyOverview,
    "research_pass": ResearchPass,
    "module_01_gate": Module01Gate,
    "module_03_revenue_model": Module03RevenueModel,
    "module_04_pain_points": Module04PainPoints,
    "module_05_corporate_structure": Module05CorporateStructure,
    "module_06_structural_news": Module06StructuralNews,
    "module_07_trigger_events": Module07TriggerEvents,
    "module_09_creative_reality": Module09CreativeReality,
    "module_10_ad_library": Module10AdLibrary,
    "module_12_competitor_snapshot": Module12CompetitorSnapshot,
    "module_13_industry_pulse": Module13IndustryPulse,
    "module_14_hiring_signal": Module14HiringSignal,
}

# Phase 1 task set — passed by name to --tasks for the recommended monthly run.
# Order matters:
#   1. module_01_gate runs first; short-circuits to out_of_scope if it fails
#   2. research_pass runs second on accounts that pass the gate, gathers shared context
#   3. synthesis-only modules read research_pass output (no own tools, single LLM call)
#   4. modules with their own tools (9, 14) run their own agent loops
PHASE1_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_03_revenue_model",
    "module_05_corporate_structure",
    "module_06_structural_news",
    "module_07_trigger_events",
    "module_09_creative_reality",
    "module_12_competitor_snapshot",
    "module_13_industry_pulse",
    "module_14_hiring_signal",
]

# Phase 2 — module 10 (ad library) + module 4 (pain-point synthesis).
# Order matters: module 04 runs LAST because it synthesizes from modules 1, 3,
# 7, 9, 10, 14 outputs — all of which must already be in the context envelope.
# Module 10 runs after module 9 so creative-posture context (in-house signal +
# named agencies) is in the envelope when module 10 classifies audience.
PHASE2_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_03_revenue_model",
    "module_05_corporate_structure",
    "module_06_structural_news",
    "module_07_trigger_events",
    "module_09_creative_reality",
    "module_10_ad_library",
    "module_12_competitor_snapshot",
    "module_13_industry_pulse",
    "module_14_hiring_signal",
    "module_04_pain_points",
]

# Tasks that act as gates — if their gate_passes() returns False, downstream
# tasks for that account are skipped. The orchestrator inspects this set.
GATE_TASKS: set[str] = {"module_01_gate"}


__all__ = ["Task", "TaskResult", "TASK_REGISTRY"]
