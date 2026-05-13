"""Tests for ResearchPass output parsing — the v2.0.0 split-format fix.

The prior v1.4.x format put raw_research INSIDE a JSON string field. When the
model emitted an unescaped `"` inside the markdown prose, the bracket-balanced
JSON parser flipped its in_string state and the entire output was unrecoverable
(Mendix #706 and Roblox #557 on 2026-05-12 — both ~5k output tokens, well under
the 8192 cap, so it wasn't a truncation issue).

v2.0.0 splits the output: small JSON for structured fields + a delimited
`<<<RAW_RESEARCH>>> ... <<<END_RAW_RESEARCH>>>` block for the prose. The JSON
parser never sees the prose, so escape bugs in markdown content can't break it.
"""

from __future__ import annotations

import prompts.research_pass as research_prompt
from tasks.base import _extract_json
from tasks.research_pass import ResearchPass


# ---- canonical v2.0.0 format ----

_V2_OUTPUT = """```json
{
  "company_name": "Mendix",
  "company_website": "https://www.mendix.com/",
  "linkedin_company_url": "https://www.linkedin.com/company/mendix/",
  "facebook_page_url": null,
  "tiktok_handle": null,
  "greenhouse_slug": null,
  "sources": ["https://www.mendix.com/about/", "https://en.wikipedia.org/wiki/Mendix"],
  "confidence": "high"
}
```

<<<RAW_RESEARCH>>>
## 1. Company Overview

Mendix is an enterprise-grade "low-code" application platform founded in 2005
in Rotterdam. Acquired by Siemens in 2018 for $730M.

The CEO described it as "the application platform for the AI era" in a
2026-02-14 keynote. Quotes "everywhere" — and they don't break the parser
because we're outside the JSON now.

## 2. Recent News

2026-03-10: launched "Mendix 11" with native LLM integration.
<<<END_RAW_RESEARCH>>>
"""


def test_post_parse_extracts_delimited_raw_research() -> None:
    """The canonical v2.0.0 path: JSON parses cleanly, raw_research arrives
    via the delimited block (with quotes embedded — the failure mode that bit
    Mendix)."""
    output = _extract_json(_V2_OUTPUT)
    assert output is not None
    assert "raw_research" not in output  # not in the JSON yet

    enriched = ResearchPass().post_parse(_V2_OUTPUT, output)

    assert enriched["company_name"] == "Mendix"
    assert enriched["linkedin_company_url"] == "https://www.linkedin.com/company/mendix/"
    assert "Rotterdam" in enriched["raw_research"]
    assert '"low-code"' in enriched["raw_research"]
    assert '"the application platform for the AI era"' in enriched["raw_research"]
    assert '"Mendix 11"' in enriched["raw_research"]


def test_post_parse_handles_unescaped_quotes_in_prose() -> None:
    """The exact failure mode: prose containing the kind of quote-heavy
    markdown that broke v1.x. With the delimited block this is no longer a
    parser problem."""
    text = """```json
{"company_name": "X", "company_website": null, "linkedin_company_url": null,
 "facebook_page_url": null, "tiktok_handle": null, "greenhouse_slug": null,
 "sources": [], "confidence": "low"}
```

<<<RAW_RESEARCH>>>
The CFO said "we're betting the company on AI" and the press called it
"the most aggressive pivot in enterprise SaaS this year". Mid-paragraph
quotes like "this" and {weird braces} and \\backslashes — none of these
should break anything because we're outside JSON.
<<<END_RAW_RESEARCH>>>
"""
    output = _extract_json(text)
    assert output is not None
    enriched = ResearchPass().post_parse(text, output)
    assert "betting the company on AI" in enriched["raw_research"]
    assert "{weird braces}" in enriched["raw_research"]
    assert "\\backslashes" in enriched["raw_research"]


def test_post_parse_missing_delimiter_yields_empty_raw_research() -> None:
    """If the model forgets the delimiters, raw_research is empty rather than
    None — keeps downstream synthesis tasks running (they degrade with
    confidence=low via the existing `synthesis_user_message` fallback)."""
    text = """```json
{"company_name": "X", "company_website": null, "linkedin_company_url": null,
 "facebook_page_url": null, "tiktok_handle": null, "greenhouse_slug": null,
 "sources": [], "confidence": "low"}
```
"""
    output = _extract_json(text)
    enriched = ResearchPass().post_parse(text, output)
    assert enriched["raw_research"] == ""


def test_post_parse_backwards_compat_inline_raw_research() -> None:
    """If the model uses the v1.x inline format (raw_research inside the JSON)
    AND the JSON happens to parse, post_parse keeps the inline value rather
    than overwriting it with empty string. Defensive — any model that
    correctly emits the v2.0.0 format will trip the delimiter branch first."""
    output = {
        "company_name": "X",
        "raw_research": "(legacy inline prose)",
        "sources": [],
        "confidence": "high",
    }
    enriched = ResearchPass().post_parse(
        '```json\n{"company_name": "X"}\n```\n', output,
    )
    assert enriched["raw_research"] == "(legacy inline prose)"


def test_post_parse_delimiter_takes_precedence_over_inline() -> None:
    """When the model emits BOTH (defensively or by accident), the delimited
    block wins. It's the v2.0.0 canonical path — and is the one that doesn't
    suffer from string-escape bugs."""
    raw_text = """```json
{"company_name": "X", "raw_research": "stale inline copy"}
```

<<<RAW_RESEARCH>>>
fresh delimited copy
<<<END_RAW_RESEARCH>>>
"""
    output = _extract_json(raw_text)
    enriched = ResearchPass().post_parse(raw_text, output)
    assert enriched["raw_research"] == "fresh delimited copy"


# ---- prompt v2.0.0 contract ----

def test_prompt_version_is_v2() -> None:
    assert research_prompt.VERSION.startswith("v2.")


def test_prompt_describes_split_output_format() -> None:
    p = research_prompt.SYSTEM_PROMPT
    assert "<<<RAW_RESEARCH>>>" in p
    assert "<<<END_RAW_RESEARCH>>>" in p
    assert "JSON FIRST" in p or "JSON block" in p
    assert "preamble" in p.lower()


def test_prompt_no_longer_includes_raw_research_in_json_example() -> None:
    """The prompt's JSON example must NOT show raw_research as a field —
    that's exactly the regression we're protecting against."""
    p = research_prompt.SYSTEM_PROMPT
    # Find the example JSON block in the prompt and check raw_research isn't
    # listed as one of its fields. The prompt may still mention the WORD
    # raw_research in surrounding instructions, so we look only inside the
    # ```json``` example.
    import re
    example = re.search(r"```json\n(.*?)\n```", p, re.DOTALL)
    assert example is not None, "prompt must contain a json example"
    assert '"raw_research"' not in example.group(1)
