"""Notion CRM adapter — read accounts, write properties + page-body blocks.

Property names + option vocabularies are exact strings (Notion is case-sensitive
and a rename here = a one-line change everywhere).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from notion_client import Client as NotionClient

import config
from rate_limit import NOTION_LIMITER


# ---- Property names ----
PROP_ACCOUNT_NAME = "Account Name"
PROP_REP = "Rep"
PROP_PRIORITY_TYPE = "Priority Type"
PROP_SIZE = "Size"
PROP_BUYING_SIGNALS = "Buying Signals"
PROP_BUYING_INTENT = "Buying Intent"
PROP_LEAD_SIGNAL = "Lead Signal"
PROP_NEW_HIRE = "New Hire"
PROP_COMPANY_STRUCTURE = "Company Structure"  # no trailing space — must match Notion exactly
PROP_STRUCTURE_NOTES = "Structure Notes"
PROP_SISTER_CHILD = "sister/child"
PROP_PARENT = "Parent"
PROP_PAIN_POINT_TAGS = "Pain Point Tags"
PROP_NOTES = "Notes"

# Agent-managed
PROP_LAST_RESEARCHED = "Last Researched"
PROP_RESEARCH_CONFIDENCE = "Research Confidence"
PROP_RESEARCH_STATUS = "Research Status"

# ---- Option vocabularies ----
# 2026-05-11 role swap: `Buying Signals` is now the agent-only multi_select,
# `Buying Intent` is human-managed (BDR-curated tags). The agent never writes
# to PROP_BUYING_INTENT; PROP_BUYING_SIGNALS is overwritten in full each run
# (no reconcile-with-existing needed because the agent is the sole owner).
SIZE_OPTIONS = {"<1000", "1000-2000", "2000-5000", "5000+"}
BUYING_SIGNAL_OPTIONS = {
    "funding round", "active creative jobs", "rebrand/campaign",
    "agency switch", "AI initiative", "industry movement",
    "hiring", "downsizing",
}
BUYING_INTENT_OPTIONS = {
    "unify mod", "unify high", "sales nav mod", "sales nav high",
    "industry", "cluster", "CW competitor", "MQA",
}
# Module 4 pain-point tags. Agent-managed via PROP_PAIN_POINT_TAGS. Each tag
# names a kind of strategic pain that maps to a Superside service angle —
# narrative, not mechanical-signal-derived (see prompts/module_04_pain_points.py).
PAIN_POINT_TAG_OPTIONS = {
    "creative production", "localization", "new territory", "strategy",
    "audience education", "competitive displacement", "brand evolution",
    "launch surge", "AI receptivity", "post-layoff overflow",
}
LEAD_SIGNAL_OPTIONS = {"TOFU", "MQL", "call request", "new hire"}
RESEARCH_STATUS_OPTIONS = {"pending", "done", "needs_review", "failed", "out_of_scope"}
RESEARCH_CONFIDENCE_OPTIONS = {"high", "medium", "low", "failed"}

REP_KATARINA = "Katarina"
PRIORITY_A = "Priority A"


# ---- Schema check ----
# Notion property type contract. Keep this list aligned with the database schema.
# Note: there is intentionally NO "Employee Count" number property — the model
# returns an integer estimate, and code deterministically buckets it into the
# existing `Size` select (Fix Appendix #21 + 2026-05-09 follow-up). One Notion
# field per research decision; no new schema burden on the Money Moguls CRM.
EXPECTED_NOTION_PROPERTIES: dict[str, str] = {
    PROP_ACCOUNT_NAME: "title",
    PROP_LAST_RESEARCHED: "date",
    PROP_RESEARCH_STATUS: "select",
    PROP_RESEARCH_CONFIDENCE: "select",
    PROP_SIZE: "select",
    PROP_PARENT: "rich_text",
    PROP_SISTER_CHILD: "rich_text",
    PROP_BUYING_SIGNALS: "multi_select",
    PROP_BUYING_INTENT: "multi_select",
    PROP_PAIN_POINT_TAGS: "multi_select",
    PROP_STRUCTURE_NOTES: "rich_text",
}


# Notion appends a section per run; we tag the heading so future runs can locate
# and replace the previous one (Fix Appendix #2 — idempotent appends).
#
# Heading shapes (2026-05-12 update):
#   unlabeled : "Research — YYYY-MM-DD"                     ← default
#   labeled   : "Research — YYYY-MM-DD — <label>"           ← --label flag
#
# Label-aware replacement: a labeled run only archives the prior heading with
# the same label, so multiple variants (e.g. "baseline" + "haiku-synthesis")
# coexist on the same page for side-by-side comparison.
AGENT_SECTION_PREFIX = "Research — "
AGENT_SECTION_LABEL_SEP = " — "


def build_section_heading_text(today_iso: str, label: str | None = None) -> str:
    """Return the heading_2 text the agent emits at the top of every research
    section. Pure function so writeback + tests share one source of truth."""
    if label:
        return f"{AGENT_SECTION_PREFIX}{today_iso}{AGENT_SECTION_LABEL_SEP}{label}"
    return f"{AGENT_SECTION_PREFIX}{today_iso}"


def _heading_matches_label(text: str, label: str | None) -> bool:
    """True iff `text` is an agent-emitted research heading with the given
    label (None = unlabeled). Used by find_latest_agent_section to scope
    replacement to a single variant on multi-variant pages.

    Unlabeled match: exactly `Research — YYYY-MM-DD` (no trailing label).
    Labeled match  : ends with `<sep><label>` after a date prefix.
    """
    if not text.startswith(AGENT_SECTION_PREFIX):
        return False
    tail = text[len(AGENT_SECTION_PREFIX):]
    if label is None:
        # Must be ONLY a date — no second `<sep>` component.
        return AGENT_SECTION_LABEL_SEP not in tail
    suffix = f"{AGENT_SECTION_LABEL_SEP}{label}"
    if not text.endswith(suffix):
        return False
    # Must have a date component before the label, i.e. at least
    # `Research — XXXX-XX-XX — <label>` shape (not `Research — <label>`).
    date_part = text[len(AGENT_SECTION_PREFIX):-len(suffix)]
    return bool(date_part)


@dataclass
class Account:
    page_id: str
    name: str
    rep: str | None
    priority_type: str | None
    last_researched: str | None  # ISO date string


class NotionCRM:
    def __init__(self, token: str | None = None, database_id: str | None = None):
        self.client = NotionClient(auth=token or config.NOTION_API_KEY)
        self.database_id = database_id or config.NOTION_DATABASE_ID

    def list_accounts(
        self,
        rep: str | None = None,
        priority_type: str | None = None,
        researched_before: str | None = None,
        limit: int | None = None,
    ) -> list[Account]:
        and_filters: list[dict[str, Any]] = []
        if rep:
            and_filters.append({"property": PROP_REP, "select": {"equals": rep}})
        if priority_type:
            and_filters.append({"property": PROP_PRIORITY_TYPE, "select": {"equals": priority_type}})
        if researched_before:
            and_filters.append({
                "or": [
                    {"property": PROP_LAST_RESEARCHED, "date": {"is_empty": True}},
                    {"property": PROP_LAST_RESEARCHED, "date": {"before": researched_before}},
                ]
            })

        query: dict[str, Any] = {"database_id": self.database_id}
        if and_filters:
            query["filter"] = {"and": and_filters} if len(and_filters) > 1 else and_filters[0]

        results: list[Account] = []
        cursor = None
        while True:
            if cursor:
                query["start_cursor"] = cursor
            with NOTION_LIMITER:
                response = self.client.databases.query(**query)
            for page in response.get("results", []):
                results.append(_account_from_page(page))
                if limit and len(results) >= limit:
                    return results
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
        return results

    def update_properties(self, page_id: str, properties: dict[str, Any]) -> None:
        with NOTION_LIMITER:
            self.client.pages.update(page_id=page_id, properties=properties)

    def append_blocks(self, page_id: str, blocks: Iterable[dict[str, Any]]) -> None:
        blocks_list = list(blocks)
        if not blocks_list:
            return
        # Notion API caps appends at 100 blocks per call.
        for i in range(0, len(blocks_list), 100):
            with NOTION_LIMITER:
                self.client.blocks.children.append(
                    block_id=page_id, children=blocks_list[i:i + 100]
                )

    # ---- Idempotent agent section management (Fix Appendix #2) ----

    def find_latest_agent_section(
        self,
        page_id: str,
        *,
        label: str | None = None,
    ) -> list[str]:
        """Return block IDs covering the most recent agent-emitted section
        whose heading_2 matches the given label.

        The agent emits a heading_2 of the form `Research — YYYY-MM-DD[ — label]`,
        then a body of blocks, then a divider. We pick the LAST such heading_2
        whose label matches and return every block from that heading through
        the next divider (inclusive). On rerun we archive these IDs before
        appending fresh content so research sections never duplicate.

        With label=None: matches only unlabeled headings (no trailing label).
        With label="x": matches only headings ending in ` — x`. Different-
        labeled sections coexist on the same page for variant comparison.
        """
        cursor: str | None = None
        all_children: list[dict[str, Any]] = []
        while True:
            with NOTION_LIMITER:
                resp = self.client.blocks.children.list(
                    block_id=page_id, start_cursor=cursor
                )
            all_children.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        # Find the LAST heading_2 whose text matches the label.
        heading_idx = -1
        for i, block in enumerate(all_children):
            if block.get("type") != "heading_2":
                continue
            rich = block.get("heading_2", {}).get("rich_text", []) or []
            text = "".join(rt.get("plain_text", "") for rt in rich)
            if _heading_matches_label(text, label):
                heading_idx = i
        if heading_idx < 0:
            return []

        # Walk forward to and including the next divider (or end of page).
        # If we hit ANOTHER agent research heading first, stop before it so
        # we don't archive the next variant's content alongside ours.
        end_idx = len(all_children)
        for j in range(heading_idx + 1, len(all_children)):
            b = all_children[j]
            if b.get("type") == "divider":
                end_idx = j + 1  # include the divider itself
                break
            if b.get("type") == "heading_2":
                rich = b.get("heading_2", {}).get("rich_text", []) or []
                text = "".join(rt.get("plain_text", "") for rt in rich)
                if text.startswith(AGENT_SECTION_PREFIX):
                    end_idx = j  # stop BEFORE the next agent heading
                    break

        return [b["id"] for b in all_children[heading_idx:end_idx]]

    def replace_latest_research_section(
        self,
        page_id: str,
        blocks: Iterable[dict[str, Any]],
        *,
        label: str | None = None,
    ) -> None:
        """Idempotent replacement: archive the previous agent section matching
        `label`, then append. With label=None this matches unlabeled sections
        only — labeled variants on the same page survive.
        """
        old_block_ids = self.find_latest_agent_section(page_id, label=label)
        for block_id in old_block_ids:
            try:
                with NOTION_LIMITER:
                    self.client.blocks.update(block_id=block_id, archived=True)
            except Exception:
                # Best-effort: a block may already be archived/missing on retry.
                pass
        self.append_blocks(page_id, blocks)

    # ---- Schema check (Fix Appendix #20) ----

    def validate_schema(
        self, expected: dict[str, str] | None = None,
    ) -> None:
        """Fail loudly if Notion's database properties drift from EXPECTED_NOTION_PROPERTIES.

        Run at startup so a renamed-or-typo'd property halts the run BEFORE we
        write garbage to Notion. Reports both missing names and wrong types.
        """
        expected = expected if expected is not None else EXPECTED_NOTION_PROPERTIES
        with NOTION_LIMITER:
            db = self.client.databases.retrieve(database_id=self.database_id)
        actual = db.get("properties", {})
        missing = [name for name in expected if name not in actual]
        wrong_type = [
            f"{name} (got={actual[name].get('type')!r}, expected={typ!r})"
            for name, typ in expected.items()
            if name in actual and actual[name].get("type") != typ
        ]
        if missing or wrong_type:
            raise RuntimeError(
                "Notion schema drift. "
                f"Missing={missing}; wrong_type={wrong_type}. "
                "Update the Notion database to match EXPECTED_NOTION_PROPERTIES, "
                "or update the constant if the rename is intentional."
            )


# ---- Page-body block helpers (used by tasks + orchestrator) ----

def paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def bullet(text: str, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rich_text": [{"type": "text", "text": {"content": text}}],
    }
    if children:
        body["children"] = children
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": body,
    }


def heading_2(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def heading_3(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


# ---- internal ----

def _account_from_page(page: dict[str, Any]) -> Account:
    props = page.get("properties", {})
    return Account(
        page_id=page["id"],
        name=_extract_title(props.get(PROP_ACCOUNT_NAME)),
        rep=_extract_select(props.get(PROP_REP)),
        priority_type=_extract_select(props.get(PROP_PRIORITY_TYPE)),
        last_researched=_extract_date(props.get(PROP_LAST_RESEARCHED)),
    )


def _extract_title(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("title", [])).strip()


def _extract_select(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _extract_date(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    d = prop.get("date")
    return d.get("start") if d else None
