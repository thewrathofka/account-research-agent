"""Task registry. New tasks register their class here.

CompanyOverview is the v0.0 single-task MVP; kept only for legacy test
fixtures (test_gate.py / test_providers.py). It is NOT part of PHASE1_TASKS
or PHASE2_TASKS and should never be passed to live runs.
"""

from __future__ import annotations

from tasks._legacy import CompanyOverview
from tasks.base import Task, TaskResult
from tasks.module_01 import Module01Gate
from tasks.module_02 import Module02RevenueModel
from tasks.module_03 import Module03PainPoints
from tasks.module_04 import Module04CorporateStructure
from tasks.module_05 import Module05StructuralNews
from tasks.module_06 import Module06TriggerEvents
from tasks.module_07 import Module07CreativeReality
from tasks.module_08 import Module08AdLibrary
from tasks.module_12 import Module12CompetitorSnapshot
from tasks.module_13 import Module13IndustryPulse
from tasks.module_14 import Module14HiringSignal
from tasks.research_pass import ResearchPass


TASK_REGISTRY: dict[str, type[Task]] = {
    "company_overview": CompanyOverview,
    "research_pass": ResearchPass,
    "module_01_gate": Module01Gate,
    "module_02_revenue_model": Module02RevenueModel,
    "module_03_pain_points": Module03PainPoints,
    "module_04_corporate_structure": Module04CorporateStructure,
    "module_05_structural_news": Module05StructuralNews,
    "module_06_trigger_events": Module06TriggerEvents,
    "module_07_creative_reality": Module07CreativeReality,
    "module_08_ad_library": Module08AdLibrary,
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
    "module_02_revenue_model",
    "module_04_corporate_structure",
    "module_05_structural_news",
    "module_06_trigger_events",
    "module_07_creative_reality",
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
    "module_02_revenue_model",
    "module_04_corporate_structure",
    "module_05_structural_news",
    "module_06_trigger_events",
    "module_07_creative_reality",
    "module_08_ad_library",
    "module_12_competitor_snapshot",
    "module_13_industry_pulse",
    "module_14_hiring_signal",
    "module_03_pain_points",
]

# Tasks that act as gates — if their gate_passes() returns False, downstream
# tasks for that account are skipped. The orchestrator inspects this set.
# Both gates are run-once: see Task.run_once + orchestrator's
# _should_skip_for_freshness branch that consults runs.db for prior success.
GATE_TASKS: set[str] = {"module_01_gate"}


# ---- Cadence-tier task sets (Phase A — 2026-05-13) ----
#
# Splits PHASE2_TASKS into refresh cadences so the GHA scheduler / Kali's
# `/schedule` skill can run subsets at the right frequency. Each tier
# includes the gate + research_pass at the top so downstream synthesis
# modules in the tier have the context envelope they need.

# Daily-tier news + buying-signal modules. Highest sales-relevance for
# unpredictable, time-sensitive events: M&A, bankruptcy, funding rounds,
# agency switches, AI initiatives, new senior creative hires.
DAILY_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_05_structural_news",
    "module_06_trigger_events",
    "module_14_hiring_signal",
]

# Weekly-tier: full ATS snapshot diff + category-level industry pulse.
WEEKLY_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_13_industry_pulse",
    "module_14_hiring_signal",
]

# Monthly-tier: slow-moving creative posture + ad library + pain-point
# synthesis. Module 04 must run AFTER the modules it reads from in the
# context envelope (gate, revenue model, competitors) — so a monthly run
# only synthesizes well when its dependencies have ALSO run within the
# month. Daily/weekly partial runs do not invalidate the monthly's M04.
MONTHLY_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_07_creative_reality",
    "module_08_ad_library",
    "module_03_pain_points",
]

# Quarterly-tier: corporate structure, competitive landscape, revenue model.
# Years between meaningful changes — cheap to keep stale.
QUARTERLY_TASKS = [
    "module_01_gate",
    "research_pass",
    "module_02_revenue_model",
    "module_04_corporate_structure",
    "module_12_competitor_snapshot",
]


__all__ = [
    "Task", "TaskResult", "TASK_REGISTRY",
    "PHASE1_TASKS", "PHASE2_TASKS",
    "DAILY_TASKS", "WEEKLY_TASKS", "MONTHLY_TASKS", "QUARTERLY_TASKS",
    "GATE_TASKS",
]
