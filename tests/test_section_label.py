"""Tests for label-aware research section identification + replacement.

Variant runs (e.g. `--label baseline` vs `--label haiku-synthesis`) MUST
coexist on the same Notion page. `find_latest_agent_section` must scope its
match to the given label so a labeled rerun only archives its own prior
section.
"""

from __future__ import annotations

import crm as crm_module
from crm import (
    AGENT_SECTION_LABEL_SEP,
    AGENT_SECTION_PREFIX,
    _heading_matches_label,
    build_section_heading_text,
)
from orchestrator import _research_section_blocks
from tasks.base import TaskResult


# ---- build_section_heading_text ----

def test_heading_text_unlabeled() -> None:
    assert build_section_heading_text("2026-05-12") == "Research — 2026-05-12"


def test_heading_text_labeled() -> None:
    assert build_section_heading_text("2026-05-12", "phase2-baseline") == (
        "Research — 2026-05-12 — phase2-baseline"
    )


def test_heading_text_label_none_acts_like_unlabeled() -> None:
    assert build_section_heading_text("2026-05-12", None) == "Research — 2026-05-12"


def test_heading_text_empty_label_acts_like_unlabeled() -> None:
    """An empty string sneaking through degrades to unlabeled (defensive)."""
    assert build_section_heading_text("2026-05-12", "") == "Research — 2026-05-12"


def test_constants_define_expected_strings() -> None:
    """If these change, every existing Notion page with an old heading shape
    will stop being recognized — keep these constants stable across releases
    OR ship a migration."""
    assert AGENT_SECTION_PREFIX == "Research — "
    assert AGENT_SECTION_LABEL_SEP == " — "


# ---- _heading_matches_label ----

def test_match_unlabeled_only_matches_date_only_headings() -> None:
    assert _heading_matches_label("Research — 2026-05-12", None) is True
    assert _heading_matches_label("Research — 2026-05-12 — baseline", None) is False
    assert _heading_matches_label("Research — 2026-05-12 — haiku-synth", None) is False


def test_match_labeled_only_matches_that_label() -> None:
    txt = "Research — 2026-05-12 — baseline"
    assert _heading_matches_label(txt, "baseline") is True
    assert _heading_matches_label(txt, "haiku-synthesis") is False
    assert _heading_matches_label(txt, None) is False


def test_match_rejects_non_agent_headings() -> None:
    assert _heading_matches_label("Notes — Random", None) is False
    assert _heading_matches_label("Research", None) is False
    assert _heading_matches_label("", None) is False
    # Heading shaped like a label-only without a date part: also reject.
    assert _heading_matches_label("Research — baseline", "baseline") is False


def test_match_rejects_label_with_extra_suffix() -> None:
    """`--label foo` must NOT collide with `--label foo-bar`."""
    txt = "Research — 2026-05-12 — foo-bar"
    assert _heading_matches_label(txt, "foo") is False
    assert _heading_matches_label(txt, "foo-bar") is True


# ---- _research_section_blocks emits labeled heading_2 ----

def _make_result(*, section: str, page_blocks: list[dict] | None = None) -> TaskResult:
    return TaskResult(
        task_name="t", output={}, confidence="medium", fields={},
        page_blocks=page_blocks or [],
        section=section, subsection=None,
        sources=[], search_count=0,
        input_tokens=0, output_tokens=0,
        duration_seconds=0.0,
        prompt_version="v1", provider_name="anthropic",
        citations=[],
    )


def _first_heading2_text(blocks: list[dict]) -> str:
    for b in blocks:
        if b.get("type") == "heading_2":
            rich = b["heading_2"].get("rich_text", [])
            return "".join(r["text"]["content"] for r in rich)
    raise AssertionError("no heading_2 in blocks")


def test_research_section_blocks_unlabeled_heading() -> None:
    blocks = _research_section_blocks([_make_result(section="Overview")])
    text = _first_heading2_text(blocks)
    assert text.startswith("Research — ")
    assert AGENT_SECTION_LABEL_SEP not in text[len("Research — "):], (
        f"unlabeled heading must not have a trailing label, got: {text}"
    )


def test_research_section_blocks_labeled_heading() -> None:
    blocks = _research_section_blocks(
        [_make_result(section="Overview")],
        label="phase2-baseline",
    )
    text = _first_heading2_text(blocks)
    assert text.endswith(" — phase2-baseline"), text
    assert _heading_matches_label(text, "phase2-baseline") is True
    assert _heading_matches_label(text, None) is False


# ---- find_latest_agent_section is exercised via heading-match unit tests
# above; an end-to-end test would need a Notion mock, but the unit test
# coverage above is what guards the critical correctness property:
# unlabeled and labeled headings don't accidentally match each other.


def test_two_labels_do_not_match_each_other() -> None:
    """Independent variants on the same page must never archive each other."""
    a = build_section_heading_text("2026-05-12", "baseline")
    b = build_section_heading_text("2026-05-12", "haiku-synth")
    assert _heading_matches_label(a, "haiku-synth") is False
    assert _heading_matches_label(b, "baseline") is False
    # And each label DOES match its own heading.
    assert _heading_matches_label(a, "baseline") is True
    assert _heading_matches_label(b, "haiku-synth") is True


def test_unlabeled_section_does_not_match_any_label() -> None:
    """An old unlabeled section must not be archived by a labeled rerun."""
    txt = build_section_heading_text("2026-05-12")
    assert _heading_matches_label(txt, "anything") is False


def test_labeled_section_does_not_match_unlabeled_rerun() -> None:
    """A labeled section must not be archived by a future unlabeled rerun."""
    txt = build_section_heading_text("2026-05-12", "baseline")
    assert _heading_matches_label(txt, None) is False


# Sanity: the constants from crm import correctly at module-load time so
# any test importing them sees the same string the orchestrator sees.
def test_module_constants_consistent_with_crm_module() -> None:
    assert crm_module.AGENT_SECTION_PREFIX == "Research — "
    assert crm_module.AGENT_SECTION_LABEL_SEP == " — "
