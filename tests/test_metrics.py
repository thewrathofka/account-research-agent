"""Unit tests for evals.metrics scoring functions."""

from __future__ import annotations

from evals.metrics import (
    score_confidence_calibration, score_field_coverage,
    score_schema_compliance, score_source_verification,
)


SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["confidence", "sources"],
    "properties": {
        "confidence": {"type": "string"},
        "sources": {"type": "array"},
        "size_band": {"type": ["string", "null"]},
    },
}


def test_schema_compliance_passes_valid_output():
    out = {"confidence": "high", "sources": ["http://x.com"]}
    assert score_schema_compliance(out, SIMPLE_SCHEMA) is True


def test_schema_compliance_fails_missing_required_key():
    out = {"sources": []}  # missing 'confidence'
    assert score_schema_compliance(out, SIMPLE_SCHEMA) is False


def test_field_coverage_counts_only_non_empty_values():
    out = {"confidence": "high", "sources": [], "size_band": None}
    # confidence: filled. sources: empty list. size_band: None.
    cov = score_field_coverage(out, SIMPLE_SCHEMA)
    assert cov == 1 / 3


def test_calibration_high_confidence_match_passes():
    out = {"confidence": "high", "size_band": "5000+", "operates_in_eu_or_na": True}
    golden = {"expected": {"size_band": "5000+", "operates_in_eu_or_na": True}}
    passes, mismatches = score_confidence_calibration(out, golden)
    assert passes is True
    assert mismatches == []


def test_calibration_high_confidence_mismatch_fails():
    out = {"confidence": "high", "size_band": "<1000"}
    golden = {"expected": {"size_band": "5000+"}}
    passes, mismatches = score_confidence_calibration(out, golden)
    assert passes is False
    assert any("size_band" in m for m in mismatches)


def test_calibration_low_confidence_skipped():
    """Calibration only gates 'high' claims. Low-confidence outputs always pass."""
    out = {"confidence": "low", "size_band": "wrong"}
    golden = {"expected": {"size_band": "5000+"}}
    passes, mismatches = score_confidence_calibration(out, golden)
    assert passes is True
    assert mismatches == []


def test_source_verification_rejects_empty_sources():
    assert score_source_verification({"sources": []}) is False


def test_source_verification_rejects_non_url_strings():
    assert score_source_verification({"sources": ["not-a-url"]}) is False


def test_source_verification_accepts_https_urls():
    out = {"sources": ["https://example.com/a", "http://b.org"]}
    assert score_source_verification(out) is True


def test_calibration_string_substring_match():
    """A 'high'-confidence string output passes calibration if it contains the expected substring."""
    out = {"confidence": "high", "revenue_model": "Subscription SaaS (per-seat)"}
    golden = {"expected": {"revenue_model": "Subscription"}}
    passes, _ = score_confidence_calibration(out, golden)
    assert passes is True


def test_calibration_list_overlap_match():
    """List-of-strings: at least 1 overlap counts as match."""
    out = {"confidence": "high", "competitors": ["Adyen", "PayPal", "Square"]}
    golden = {"expected": {"competitors": ["Adyen", "Block"]}}  # one overlap (Adyen)
    passes, _ = score_confidence_calibration(out, golden)
    assert passes is True
