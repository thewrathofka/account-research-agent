"""HiringSignalsTool — wraps python-jobspy to scan Indeed/LinkedIn/Glassdoor/ZipRecruiter
for active job listings at a target company.

Returns a compact text summary the model can reason over (titles + per-source counts).
For module 14 we care about creative/marketing role density; the model handles
classification from titles.

Caveats:
- jobspy hits the four boards directly; rate limits apply per source.
- Default search location is "USA" — broader location coverage costs more requests.
- Results are a snapshot; we cap to ~30 listings to keep the model context manageable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None


HIRING_SIGNALS_SCHEMA: dict[str, Any] = {
    "name": "hiring_signals",
    "description": (
        "Aggregate active job postings for a company from Indeed, LinkedIn, "
        "Glassdoor, and ZipRecruiter. Returns titles + counts. Use this BEFORE "
        "web_search to get authoritative counts of open roles. Especially good "
        "for spotting creative/marketing hiring activity."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "The company name to search for (exact match preferred).",
            },
            "results_per_source": {
                "type": "integer",
                "description": "Max listings per source. Default 10. Cap is 25.",
                "default": 10,
            },
        },
        "required": ["company"],
    },
}


@dataclass
class HiringSignalsTool:
    """Job-board aggregation tool. One instance per task → per-task call counter."""
    _count: int = 0

    @property
    def name(self) -> str:
        return HIRING_SIGNALS_SCHEMA["name"]

    @property
    def description(self) -> str:
        return HIRING_SIGNALS_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return HIRING_SIGNALS_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    def __call__(self, company: str, results_per_source: int = 10) -> str:
        self._count += 1
        if scrape_jobs is None:
            return "ERROR: python-jobspy is not installed. Cannot run hiring_signals."
        try:
            df = scrape_jobs(
                site_name=["indeed", "linkedin", "glassdoor", "zip_recruiter"],
                search_term=company,
                location="USA",
                results_wanted=min(results_per_source, 25),
                hours_old=720,  # last 30 days
                country_indeed="USA",
            )
        except Exception as e:
            return f"ERROR: jobspy scrape failed: {type(e).__name__}: {e}"

        if df is None or len(df) == 0:
            return f"No active job listings found for {company} across Indeed/LinkedIn/Glassdoor/ZipRecruiter."

        # Filter to results that actually mention the company (jobspy returns
        # broad keyword matches; we tighten to title/company-name matches).
        company_lower = company.lower()
        df = df[df.apply(
            lambda r: company_lower in str(r.get("company", "")).lower(),
            axis=1,
        )]
        if len(df) == 0:
            return f"No exact-company matches for {company} (broad search returned only adjacent results)."

        # Build summary the model can reason over.
        per_source_counts: dict[str, int] = {}
        titles: list[str] = []
        for _, row in df.iterrows():
            site = str(row.get("site", "?"))
            per_source_counts[site] = per_source_counts.get(site, 0) + 1
            title = str(row.get("title", "?"))
            location = str(row.get("location", "?"))
            titles.append(f"  - [{site}] {title} ({location})")

        lines = [
            f"Active job listings for {company}: {len(df)} total",
            "Per source: " + ", ".join(f"{s}={c}" for s, c in sorted(per_source_counts.items())),
            "",
            "Titles:",
            *titles[:30],  # cap output for context size
        ]
        if len(titles) > 30:
            lines.append(f"  ... and {len(titles) - 30} more")
        return "\n".join(lines)
