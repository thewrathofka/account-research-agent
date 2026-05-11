"""Tests for the orchestrator's per-section citation renumbering + link rendering."""

from __future__ import annotations

from typing import Any

import crm as crm_module
from orchestrator import (
    _citation_footnote_bullet,
    _process_section_citations,
    _research_section_blocks,
    _rewrite_block_citation_markers,
)
from tasks.base import TaskResult


def _make_result(
    *,
    task_name: str,
    section: str,
    subsection: str | None = None,
    page_blocks: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> TaskResult:
    return TaskResult(
        task_name=task_name,
        output={},
        confidence="medium",
        fields={},
        page_blocks=page_blocks or [],
        section=section,
        subsection=subsection,
        sources=[],
        search_count=0,
        input_tokens=0, output_tokens=0,
        duration_seconds=0.0,
        prompt_version="v1", provider_name="anthropic",
        citations=citations or [],
    )


# ---- _process_section_citations ----

def test_process_section_citations_renumbers_per_section_in_display_order() -> None:
    """Two tasks each have module-local [1], [2]; section-global numbering
    makes them [1], [2], [3], [4] in display order."""
    r_a = _make_result(
        task_name="a", section="Overview",
        citations=[
            {"n": 1, "title": "A1", "url": "https://example.com/a1"},
            {"n": 2, "title": "A2", "url": "https://example.com/a2"},
        ],
    )
    r_b = _make_result(
        task_name="b", section="Overview",
        citations=[
            {"n": 1, "title": "B1", "url": "https://example.com/b1"},
            {"n": 2, "title": "B2", "url": "https://example.com/b2"},
        ],
    )
    remaps, section_citations = _process_section_citations([r_a, r_b])
    # Display order: A then B → section numbers 1, 2 (A), 3, 4 (B)
    assert remaps["a"][1]["n"] == 1
    assert remaps["a"][2]["n"] == 2
    assert remaps["b"][1]["n"] == 3
    assert remaps["b"][2]["n"] == 4
    assert [c["n"] for c in section_citations] == [1, 2, 3, 4]
    assert [c["url"] for c in section_citations] == [
        "https://example.com/a1", "https://example.com/a2",
        "https://example.com/b1", "https://example.com/b2",
    ]


def test_process_section_citations_dedupes_same_url_across_tasks() -> None:
    """If two tasks cite the same URL, it appears in the section footnote
    list ONCE, and both tasks' [N] markers map to the same section number."""
    r_a = _make_result(
        task_name="a", section="News",
        citations=[{"n": 1, "title": "Shared", "url": "https://shared.example.com"}],
    )
    r_b = _make_result(
        task_name="b", section="News",
        citations=[{"n": 1, "title": "Shared from B", "url": "https://shared.example.com"}],
    )
    remaps, section_citations = _process_section_citations([r_a, r_b])
    assert remaps["a"][1]["n"] == 1
    assert remaps["b"][1]["n"] == 1, "Same URL should collapse to one section citation"
    assert len(section_citations) == 1
    assert section_citations[0]["url"] == "https://shared.example.com"


def test_process_section_citations_skips_error_tasks() -> None:
    """Tasks with error=... contribute nothing to the section numbering."""
    r_ok = _make_result(
        task_name="ok", section="Overview",
        citations=[{"n": 1, "title": "OK1", "url": "https://example.com/ok"}],
    )
    r_err = TaskResult(
        task_name="err", output=None, confidence="failed",
        fields={}, page_blocks=[], section="Overview", subsection=None,
        sources=[], search_count=0, input_tokens=0, output_tokens=0,
        duration_seconds=0.0, prompt_version="v1", provider_name="anthropic",
        citations=[{"n": 1, "title": "should-skip", "url": "https://err.example.com"}],
        error="boom",
    )
    _remaps, section_citations = _process_section_citations([r_err, r_ok])
    assert len(section_citations) == 1
    assert section_citations[0]["url"] == "https://example.com/ok"


def test_process_section_citations_skips_malformed_citations() -> None:
    """Citation entries missing n or url are silently dropped (model occasionally
    half-writes one)."""
    r = _make_result(
        task_name="x", section="Overview",
        citations=[
            {"n": 1, "title": "good", "url": "https://example.com/g"},
            {"title": "missing-n", "url": "https://example.com/x"},
            {"n": 2, "title": "missing-url"},
        ],
    )
    _remaps, section_citations = _process_section_citations([r])
    assert [c["n"] for c in section_citations] == [1]


# ---- _rewrite_block_citation_markers ----

def test_rewrite_block_markers_splits_and_links() -> None:
    blocks = [crm_module.paragraph("First [1], second [2], plain text.")]
    remap = {
        1: {"n": 1, "title": "a", "url": "https://example.com/a"},
        2: {"n": 2, "title": "b", "url": "https://example.com/b"},
    }
    out = _rewrite_block_citation_markers(blocks, remap)
    rich = out[0]["paragraph"]["rich_text"]
    # Expected spans: "First ", [1] link, ", second ", [2] link, ", plain text."
    assert [(r["text"]["content"], r["text"].get("link")) for r in rich] == [
        ("First ", None),
        ("[1]", {"url": "https://example.com/a"}),
        (", second ", None),
        ("[2]", {"url": "https://example.com/b"}),
        (", plain text.", None),
    ]


def test_rewrite_block_markers_renumbers_using_remap() -> None:
    """Module-local [1] becomes section-global [7] when the remap says so."""
    blocks = [crm_module.paragraph("Module local [1] here.")]
    remap = {1: {"n": 7, "title": "x", "url": "https://example.com/x"}}
    out = _rewrite_block_citation_markers(blocks, remap)
    rich = out[0]["paragraph"]["rich_text"]
    linked = [r for r in rich if r["text"].get("link")]
    assert len(linked) == 1
    assert linked[0]["text"]["content"] == "[7]"


def test_rewrite_block_markers_strips_unmatched() -> None:
    blocks = [crm_module.paragraph("Real [1] then fake [9].")]
    remap = {1: {"n": 1, "title": "a", "url": "https://example.com/a"}}
    out = _rewrite_block_citation_markers(blocks, remap)
    rendered = "".join(r["text"]["content"] for r in out[0]["paragraph"]["rich_text"])
    assert "[1]" in rendered
    assert "[9]" not in rendered


def test_rewrite_block_markers_leaves_heading_alone() -> None:
    """Headings shouldn't carry claims — the rewriter must not touch them."""
    blocks = [crm_module.heading_3("Overview [1] not a claim")]
    out = _rewrite_block_citation_markers(blocks, {1: {"n": 1, "title": "x", "url": "https://example.com/x"}})
    # heading_3 is not one of the rewriter's targets — block passes through
    assert out[0] == blocks[0]


def test_rewrite_block_markers_passes_through_existing_link_spans() -> None:
    """A span that already has a link annotation is left untouched (could be
    a pre-rendered hyperlink from elsewhere)."""
    blocks = [{
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "[1]", "link": {"url": "https://pre.example.com"}}},
        ]},
    }]
    out = _rewrite_block_citation_markers(blocks, {1: {"n": 5, "title": "x", "url": "https://example.com/x"}})
    rich = out[0]["paragraph"]["rich_text"]
    assert rich[0]["text"]["link"] == {"url": "https://pre.example.com"}
    assert rich[0]["text"]["content"] == "[1]"  # NOT renumbered


def test_rewrite_block_markers_handles_bulleted_list_items() -> None:
    blocks = [crm_module.bullet("Bullet claim [1].")]
    remap = {1: {"n": 1, "title": "a", "url": "https://example.com/a"}}
    out = _rewrite_block_citation_markers(blocks, remap)
    rich = out[0]["bulleted_list_item"]["rich_text"]
    linked = [r for r in rich if r["text"].get("link")]
    assert len(linked) == 1
    assert linked[0]["text"]["link"] == {"url": "https://example.com/a"}


# ---- _citation_footnote_bullet ----

def test_citation_footnote_bullet_shape() -> None:
    bullet = _citation_footnote_bullet({
        "n": 5, "title": "example.com — page", "url": "https://example.com",
    })
    rich = bullet["bulleted_list_item"]["rich_text"]
    assert rich[0]["text"]["content"] == "[5]"
    assert rich[0]["text"]["link"] == {"url": "https://example.com"}
    assert "example.com — page" in rich[1]["text"]["content"]


def test_citation_footnote_bullet_handles_missing_title() -> None:
    bullet = _citation_footnote_bullet({"n": 1, "url": "https://example.com"})
    rich = bullet["bulleted_list_item"]["rich_text"]
    assert rich[0]["text"]["content"] == "[1]"
    assert rich[1]["text"]["content"] == ""


# ---- end-to-end: _research_section_blocks ----

def test_research_section_blocks_renders_section_footnotes() -> None:
    """Two tasks in Overview each contribute citations + claims → page-body
    section has paragraph claims with rewritten linked markers + a single
    Sources footnote list at the section end."""
    r_a = _make_result(
        task_name="module_03_revenue_model", section="Overview",
        page_blocks=[crm_module.paragraph("Revenue model claim [1].")],
        citations=[{"n": 1, "title": "A1", "url": "https://example.com/a1"}],
    )
    r_b = _make_result(
        task_name="module_05_corporate_structure", section="Overview",
        page_blocks=[crm_module.paragraph("Structure claim [1].")],
        citations=[{"n": 1, "title": "B1", "url": "https://example.com/b1"}],
    )
    out = _research_section_blocks([r_a, r_b])
    # Find the Overview section content
    texts = []
    for b in out:
        if b["type"] == "paragraph":
            content = "".join(r.get("text", {}).get("content", "") for r in b["paragraph"]["rich_text"])
            texts.append(("p", content))
        elif b["type"] == "bulleted_list_item":
            content = "".join(r.get("text", {}).get("content", "") for r in b["bulleted_list_item"]["rich_text"])
            texts.append(("b", content))
        elif b["type"] == "heading_3":
            content = "".join(r.get("text", {}).get("content", "") for r in b["heading_3"]["rich_text"])
            texts.append(("h", content))
    # Expect: heading "Research — …", heading_3 "Overview", paragraph "Revenue
    # model claim [1].", paragraph "Structure claim [2].", paragraph "Sources:",
    # bullet "[1] A1", bullet "[2] B1".
    flat = " | ".join(f"{k}:{v}" for k, v in texts)
    assert "Revenue model claim [1]." in flat
    assert "Structure claim [2]." in flat  # renumbered to 2
    assert "Sources:" in flat
    assert "[1] A1" in flat
    assert "[2] B1" in flat


def test_research_section_blocks_does_not_emit_footnotes_for_section_without_citations() -> None:
    r = _make_result(
        task_name="module_03_revenue_model", section="Overview",
        page_blocks=[crm_module.paragraph("Plain claim, no markers.")],
        citations=[],
    )
    out = _research_section_blocks([r])
    sources_lines = [
        b for b in out
        if b["type"] == "paragraph"
        and "Sources:" in "".join(
            r.get("text", {}).get("content", "") for r in b["paragraph"]["rich_text"]
        )
    ]
    # The page-wide tail "Sources" is a heading_3, not a paragraph. So this
    # paragraph-typed "Sources:" line should be absent.
    assert sources_lines == []
