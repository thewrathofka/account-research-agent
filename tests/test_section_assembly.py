"""Verify the section-aware page-body assembler emits sections in the prescribed order
and groups tasks correctly.
"""

from __future__ import annotations

from typing import Any

from orchestrator import _research_section_blocks
from tasks.base import TaskResult


def _result(task_name: str, section: str, subsection: str | None,
            blocks: list[dict[str, Any]]) -> TaskResult:
    """Build a synthetic TaskResult with no error."""
    return TaskResult(
        task_name=task_name, output={"x": 1}, confidence="high",
        fields={}, page_blocks=blocks, section=section, subsection=subsection,
        sources=[],
        search_count=0, input_tokens=0, output_tokens=0, duration_seconds=0.0,
        prompt_version="v1.0.0", provider_name="fake",
    )


def _block_texts(blocks: list[dict[str, Any]]) -> list[str]:
    """Extract heading + paragraph text from a block list, in order."""
    out = []
    for b in blocks:
        if b["type"] == "heading_2":
            out.append("H2:" + b["heading_2"]["rich_text"][0]["text"]["content"])
        elif b["type"] == "heading_3":
            out.append("H3:" + b["heading_3"]["rich_text"][0]["text"]["content"])
        elif b["type"] == "paragraph":
            out.append("P:" + b["paragraph"]["rich_text"][0]["text"]["content"])
        elif b["type"] == "bulleted_list_item":
            out.append("B:" + b["bulleted_list_item"]["rich_text"][0]["text"]["content"])
        elif b["type"] == "divider":
            out.append("---")
    return out


def _para(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def test_section_order_overview_first_competitor_last():
    """Top-level sections appear in the prescribed order regardless of task ordering."""
    results = [
        _result("module_12_competitor_snapshot", "Competitor Landscape", None, [_para("comp")]),
        _result("module_02_revenue_model", "Overview", None, [_para("overview")]),
        _result("module_06_trigger_events", "News", None, [_para("news")]),
    ]
    blocks = _research_section_blocks(results)
    texts = _block_texts(blocks)
    # Find indices of the three section headings.
    overview_idx = next(i for i, t in enumerate(texts) if t == "H3:Overview")
    news_idx = next(i for i, t in enumerate(texts) if t == "H3:News")
    comp_idx = next(i for i, t in enumerate(texts) if t == "H3:Competitor Landscape")
    assert overview_idx < news_idx < comp_idx


def test_subsection_grouping_overview_headcount():
    """Module 14's section=Overview, subsection=Headcount renders as 'Overview — Headcount'
    AFTER the main Overview content."""
    results = [
        _result("module_02_revenue_model", "Overview", None, [_para("revenue body")]),
        _result("module_14_hiring_signal", "Overview", "Headcount",
                [_para("47 open roles, 8 in creative")]),
    ]
    blocks = _research_section_blocks(results)
    texts = _block_texts(blocks)
    # Overview heading appears before the Headcount sub-heading
    overview_idx = next(i for i, t in enumerate(texts) if t == "H3:Overview")
    headcount_idx = next(i for i, t in enumerate(texts) if t == "H3:Overview — Headcount")
    revenue_idx = next(i for i, t in enumerate(texts) if "revenue body" in t)
    headcount_para_idx = next(i for i, t in enumerate(texts) if "47 open roles" in t)
    # Overview block content appears between the Overview heading and the Headcount sub.
    assert overview_idx < revenue_idx < headcount_idx < headcount_para_idx


def test_no_global_sources_heading_emitted():
    """v3.0.0: the global page-bottom "Sources" heading_3 was removed —
    inline `[N]` clickable citations make the trailing URL dump redundant."""
    r1 = _result("a", "Overview", None, [])
    r1.sources = ["http://x.com", "http://y.com"]
    r2 = _result("b", "News", None, [])
    r2.sources = ["http://x.com", "http://z.com"]
    blocks = _research_section_blocks([r1, r2])
    texts = _block_texts(blocks)
    assert "H3:Sources" not in texts, (
        "global page-bottom Sources heading should no longer be emitted"
    )


def test_unknown_section_lands_in_other():
    """A task targeting an unknown section is shown under 'Other' at the end (not dropped)."""
    results = [_result("custom", "Mystery Section", None, [_para("orphan")])]
    blocks = _research_section_blocks(results)
    texts = _block_texts(blocks)
    other_idx = next(i for i, t in enumerate(texts) if t == "H3:Other")
    orphan_idx = next(i for i, t in enumerate(texts) if "orphan" in t)
    assert other_idx < orphan_idx


def test_failed_task_renders_warning_in_section():
    """Failed tasks get a warning paragraph in their declared section, not silently dropped."""
    bad = _result("module_06", "News", None, [])
    bad.error = "search timeout"
    blocks = _research_section_blocks([bad])
    texts = _block_texts(blocks)
    assert any("module_06 failed" in t and "search timeout" in t for t in texts)
