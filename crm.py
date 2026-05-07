"""Notion CRM adapter — read accounts, write properties + page-body blocks.

Property names + option vocabularies are exact strings (Notion is case-sensitive
and a rename here = a one-line change everywhere).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from notion_client import Client as NotionClient

import config


# ---- Property names ----
PROP_ACCOUNT_NAME = "Account Name"
PROP_REP = "Rep"
PROP_PRIORITY_TYPE = "Priority Type"
PROP_SIZE = "Size"
PROP_BUYING_SIGNALS = "Buying Signals"
PROP_BUYING_INTENT = "Buying Intent"
PROP_LEAD_SIGNAL = "Lead Signal"
PROP_NEW_HIRE = "New Hire"
PROP_COMPANY_STRUCTURE = "Company Structure "  # trailing space is intentional
PROP_STRUCTURE_NOTES = "Structure Notes"
PROP_PARENT_CHILD = "Parent-Child"
PROP_NAME_OF_PARENT = "Name of Parent"
PROP_NOTES = "Notes"

# Agent-managed
PROP_LAST_RESEARCHED = "Last Researched"
PROP_RESEARCH_CONFIDENCE = "Research Confidence"
PROP_RESEARCH_STATUS = "Research Status"

# ---- Option vocabularies ----
SIZE_OPTIONS = {"<1000", "1000-2000", "2000-5000", "5000+"}
BUYING_SIGNAL_OPTIONS = {
    "unify mod", "unify high", "sales nav mod", "sales nav high",
    "industry", "cluster", "CW competitor", "MQA", "hiring", "downsizing",
}
BUYING_INTENT_OPTIONS = {
    "funding round", "active creative jobs", "rebrand/campaign",
    "agency switch", "AI initiative", "industry movement",
}
LEAD_SIGNAL_OPTIONS = {"TOFU", "MQL", "call request", "new hire"}
RESEARCH_STATUS_OPTIONS = {"pending", "done", "needs_review", "failed", "out_of_scope"}
RESEARCH_CONFIDENCE_OPTIONS = {"high", "medium", "low", "failed"}

REP_KATARINA = "Katarina"
PRIORITY_A = "Priority A"


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
        self.client.pages.update(page_id=page_id, properties=properties)

    def append_blocks(self, page_id: str, blocks: Iterable[dict[str, Any]]) -> None:
        blocks_list = list(blocks)
        if not blocks_list:
            return
        # Notion API caps appends at 100 blocks per call.
        for i in range(0, len(blocks_list), 100):
            self.client.blocks.children.append(
                block_id=page_id, children=blocks_list[i:i + 100]
            )


# ---- Page-body block helpers (used by tasks + orchestrator) ----

def paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]},
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
