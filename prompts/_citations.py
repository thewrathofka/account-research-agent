"""Shared citation contract injected into every page-body-emitting prompt.

The orchestrator's per-section citation rendering (orchestrator.py:
_process_section_citations + _rewrite_block_citation_markers) expects each
module to emit:
  - Plain-text page-body content with module-local `[N]` markers placed
    after specific factual claims.
  - A `citations` array of `{n, title, url}` objects keyed by those N values.

This module gives every prompt the same wording so the contract is uniform
across modules — change CITATION_INSTRUCTIONS here, every prompt picks it up
on its next VERSION bump.

The matching JSON schema fragment is `CITATIONS_SCHEMA_FRAGMENT`. Drop it
into each prompt's `JSON_SCHEMA["properties"]` and add `"citations"` to
`JSON_SCHEMA["required"]`.
"""

from __future__ import annotations

from typing import Any


CITATION_INSTRUCTIONS = """\
## Citation requirements

Every specific factual claim in your page-body text (numbers, dates, named
events, quoted phrases, competitive moves, specific people, specific URLs
the company published) MUST be backed by a citation marker `[N]` placed
immediately after the sentence or bullet item containing the claim. Each
`[N]` references an entry in the `citations` array, numbered starting at 1.

The downstream renderer turns each `[N]` into a clickable Notion link and
collapses repeated sources across the page-body section into a single
footnote list per section.

Rules for `citations`:
- `n` is a positive integer starting at 1, used at least once in the page-body
  text. Numbers must be sequential and contiguous within the module's output
  (1, 2, 3, ...). The orchestrator will renumber them globally per section,
  so don't worry about cross-module collisions.
- `url` MUST be a URL the model actually saw — either in upstream task output
  or in the live tool results during THIS task. DO NOT invent URLs.
- `title` is a short human-readable label of the form `domain — claim`
  (e.g. `cnbc.com — Oracle Q3 FY2026 earnings`). Keep under 100 chars.
- Aim for 1-5 citations per module (more clutters the prose).
- General framing claims that summarise the overall picture do not need
  citations — only specific verifiable facts do.
- The same source can be cited multiple times in the text but should appear
  in the `citations` array only ONCE with a single `n`.

Example fragment (your full output JSON will include other fields per your
module's schema):
```json
{
  "...": "...module-specific fields...",
  "citations": [
    {"n": 1, "title": "cnbc.com — Q3 FY2026 earnings", "url": "https://www.cnbc.com/..."},
    {"n": 2, "title": "axios.com — OCI AI launch", "url": "https://www.axios.com/..."}
  ],
  "sources": ["https://www.cnbc.com/...", "https://www.axios.com/..."],
  "...": "..."
}
```

`sources` (the existing flat-URL list) stays as-is for the eval framework's
subset-of-tool-results check; it should be the same URLs that appear in
`citations[].url` (no extra ones). Empty arrays are fine when no specific
claims need citations this run."""


CITATIONS_SCHEMA_FRAGMENT: dict[str, Any] = {
    "type": "array",
    "minItems": 0, "maxItems": 12,
    "items": {
        "type": "object",
        "required": ["n", "title", "url"],
        "properties": {
            "n": {"type": "integer", "minimum": 1},
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "url": {"type": "string"},
        },
    },
}
