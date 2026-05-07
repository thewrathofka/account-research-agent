"""Task registry. New tasks register their class here.

Phase 1 will add module_01 ... module_14. For v0.1.0 (post-refactor) we keep
the legacy CompanyOverview around so the smoke test still passes.
"""

from __future__ import annotations

from tasks._legacy import CompanyOverview
from tasks.base import Task, TaskResult
from tasks.module_01 import Module01Gate
from tasks.module_03 import Module03RevenueModel
from tasks.module_05 import Module05CorporateStructure
from tasks.module_06 import Module06StructuralNews
from tasks.module_07 import Module07TriggerEvents
from tasks.module_09 import Module09CreativeReality
from tasks.module_12 import Module12CompetitorSnapshot
from tasks.module_13 import Module13IndustryPulse
from tasks.module_14 import Module14HiringSignal


TASK_REGISTRY: dict[str, type[Task]] = {
    "company_overview": CompanyOverview,
    "module_01_gate": Module01Gate,
    "module_03_revenue_model": Module03RevenueModel,
    "module_05_corporate_structure": Module05CorporateStructure,
    "module_06_structural_news": Module06StructuralNews,
    "module_07_trigger_events": Module07TriggerEvents,
    "module_09_creative_reality": Module09CreativeReality,
    "module_12_competitor_snapshot": Module12CompetitorSnapshot,
    "module_13_industry_pulse": Module13IndustryPulse,
    "module_14_hiring_signal": Module14HiringSignal,
}

# Phase 1 task set — passed by name to --tasks for the recommended monthly run.
# Order matters: module_01_gate must be first so the GATE can short-circuit.
PHASE1_TASKS = [
    "module_01_gate",
    "module_03_revenue_model",
    "module_05_corporate_structure",
    "module_06_structural_news",
    "module_07_trigger_events",
    "module_09_creative_reality",
    "module_12_competitor_snapshot",
    "module_13_industry_pulse",
    "module_14_hiring_signal",
]
PHASE1_TAVILY_TASKS = [
    t for t in PHASE1_TASKS
    if t not in ("module_09_creative_reality", "module_14_hiring_signal")
]

# Tasks that act as gates — if their gate_passes() returns False, downstream
# tasks for that account are skipped. The orchestrator inspects this set.
GATE_TASKS: set[str] = {"module_01_gate"}


__all__ = ["Task", "TaskResult", "TASK_REGISTRY"]
