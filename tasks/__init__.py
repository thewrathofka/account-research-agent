"""Task registry. New tasks register their class here.

Phase 1 will add module_01 ... module_14. For v0.1.0 (post-refactor) we keep
the legacy CompanyOverview around so the smoke test still passes.
"""

from __future__ import annotations

from tasks._legacy import CompanyOverview
from tasks.base import Task, TaskResult


TASK_REGISTRY: dict[str, type[Task]] = {
    "company_overview": CompanyOverview,
    # Phase 1 tasks register here as they ship.
}


__all__ = ["Task", "TaskResult", "TASK_REGISTRY"]
