"""HiringSignalsTool — wraps python-jobspy to scan Indeed/LinkedIn/Glassdoor/ZipRecruiter
for active job listings at a target company.

Per Fix Appendix #12, the gate covers EU + NA but the previous implementation
hard-coded country_indeed='USA'. This version takes a `countries` list (provided
by Module14 from gate.regions_present), runs one scrape per country, and merges
counts conservatively.

Per Fix Appendix #13, naive `company_lower in row['company']` match was both
loose (false positives on substring overlap, e.g. "Stripe Health Inc." matching
on the search "Stripe") and tight (missed "Stripe, Inc." vs. "Stripe"). This
version normalizes both sides — drops legal suffixes — and only accepts results
whose normalized company name equals the target. A fuzzy threshold can be added
when scoring CRM contacts directly; for our use case exact-normalized is the
safe default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None

from rate_limit import JOBSPY_LIMITER


HIRING_SIGNALS_SCHEMA: dict[str, Any] = {
    "name": "hiring_signals",
    "description": (
        "Aggregate active job postings for a company from Indeed, LinkedIn, "
        "Glassdoor, and ZipRecruiter. Returns titles + per-country counts. "
        "Use this BEFORE web_search to get authoritative counts of open roles. "
        "Especially good for spotting creative/marketing hiring activity."
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
            "countries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Countries to search in (job-board labels: USA, Canada, "
                    "UK, Germany, France, Ireland, Netherlands, Spain, Italy, "
                    "Sweden, Poland). Use the regions_present field from the "
                    "module_01_gate output. Default: ['USA']."
                ),
            },
        },
        "required": ["company"],
    },
}


# Common legal suffixes stripped from company names before matching.
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "gmbh", "plc", "corp", "corporation",
    "limited", "ag", "sa", "bv", "ab", "spa", "co", "company",
}

# Fuzzy match threshold for company name comparison (0.0–1.0). Above this we
# accept the row even without exact-normalized equality. Below this we reject.
FUZZY_THRESHOLD = 0.92


def normalize_company(name: str) -> str:
    """Normalize a company name for comparison.

    Lowercase, strip non-alphanumerics, drop legal suffixes (inc/llc/gmbh/...).
    "Stripe, Inc." → "stripe". "Acme Corp Ltd." → "acme".
    """
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(t for t in tokens if t not in LEGAL_SUFFIXES)


@dataclass
class HiringSignalsTool:
    """Job-board aggregation tool. One instance per task → per-task call counter."""
    _count: int = 0
    _observed_urls: list[str] = field(default_factory=list)

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

    @property
    def observed_urls(self) -> list[str]:
        return list(self._observed_urls)

    def __call__(
        self,
        company: str,
        results_per_source: int = 10,
        countries: list[str] | None = None,
    ) -> str:
        self._count += 1
        if scrape_jobs is None or pd is None:
            return "ERROR: python-jobspy / pandas not installed. Cannot run hiring_signals."

        countries = countries or ["USA"]
        per_country_frames: list[Any] = []
        per_country_errors: list[str] = []
        for country in countries:
            try:
                with JOBSPY_LIMITER:
                    df = scrape_jobs(
                        site_name=["indeed", "linkedin", "glassdoor", "zip_recruiter"],
                        search_term=company,
                        location=country,
                        results_wanted=min(results_per_source, 25),
                        hours_old=720,  # last 30 days
                        country_indeed=country,
                    )
            except Exception as e:
                per_country_errors.append(f"{country}: {type(e).__name__}: {e}")
                continue
            if df is not None and len(df) > 0:
                df = df.assign(_country=country)
                per_country_frames.append(df)

        if not per_country_frames:
            err_suffix = (" Errors: " + "; ".join(per_country_errors)) if per_country_errors else ""
            return (
                f"No active job listings found for {company} across "
                f"Indeed/LinkedIn/Glassdoor/ZipRecruiter in {countries}.{err_suffix}"
            )

        df = pd.concat(per_country_frames, ignore_index=True)

        # Filter on normalized company name (Fix Appendix #13).
        target = normalize_company(company)

        def _company_match(row: Any) -> bool:
            raw = str(row.get("company") or "")
            normed = normalize_company(raw)
            if not normed or not target:
                return False
            if normed == target:
                return True
            # Domain-based check when jobspy surfaces a company URL.
            url = str(row.get("company_url") or row.get("job_url") or "")
            if target.replace(" ", "") in url.lower():
                return True
            ratio = SequenceMatcher(None, normed, target).ratio()
            return ratio >= FUZZY_THRESHOLD

        df = df[df.apply(_company_match, axis=1)]
        if len(df) == 0:
            return (
                f"No exact-company matches for {company} (broad search returned "
                f"only adjacent results). Searched {countries}."
            )

        # Surface job URLs to observed_urls so the eval source check has a ledger.
        for _, row in df.iterrows():
            url = str(row.get("job_url") or "")
            if url and url not in self._observed_urls:
                self._observed_urls.append(url)

        per_country_counts: dict[str, int] = {}
        per_source_counts: dict[str, int] = {}
        titles: list[str] = []
        for _, row in df.iterrows():
            country = str(row.get("_country", "?"))
            site = str(row.get("site", "?"))
            per_country_counts[country] = per_country_counts.get(country, 0) + 1
            per_source_counts[site] = per_source_counts.get(site, 0) + 1
            title = str(row.get("title", "?"))
            location = str(row.get("location", "?"))
            titles.append(f"  - [{country}/{site}] {title} ({location})")

        lines = [
            f"Active job listings for {company}: {len(df)} total",
            "Per country: " + ", ".join(f"{c}={n}" for c, n in sorted(per_country_counts.items())),
            "Per source: " + ", ".join(f"{s}={c}" for s, c in sorted(per_source_counts.items())),
            "",
            "Titles:",
            *titles[:30],  # cap output for context size
        ]
        if len(titles) > 30:
            lines.append(f"  ... and {len(titles) - 30} more")
        if per_country_errors:
            lines.append("")
            lines.append("Country-level errors:")
            for err in per_country_errors:
                lines.append(f"  - {err}")
        return "\n".join(lines)
