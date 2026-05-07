"""Smoke tests for provider abstraction.

Uses a FakeProvider that returns canned responses so we can assert the Task
machinery works end-to-end without API calls. If these pass, the abstraction
itself is sound and any provider that conforms to LLMProvider should plug in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from providers.base import ProviderResult
from tasks._legacy import CompanyOverview


# ---- Fake tool + provider for offline tests ----

@dataclass
class FakeTool:
    name: str = "web_search"
    description: str = "fake"
    input_schema: dict = field(default_factory=lambda: {"type": "object"})
    _count: int = 0

    @property
    def call_count(self) -> int:
        return self._count

    def __call__(self, **kwargs: Any) -> str:
        self._count += 1
        return "fake search result"


@dataclass
class FakeProvider:
    """Returns a canned ProviderResult. Pretends to be the protocol."""
    name: str = "fake"
    model: str = "fake-model"
    canned_text: str = '```json\n{"company_name":"Test","summary":"x","industry":"y","confidence":"high","sources":["http://a"]}\n```'
    canned_input_tokens: int = 100
    canned_output_tokens: int = 50

    def run_loop(
        self, system_prompt, user_message, tools, max_iterations=10, tool_call_cap=None,
        model_tier="smart", cache_system_prompt=True,
    ) -> ProviderResult:
        return ProviderResult(
            text=self.canned_text,
            tool_calls_made=0,
            input_tokens=self.canned_input_tokens,
            output_tokens=self.canned_output_tokens,
            iterations=1,
            stop_reason="end_turn",
            model_used=self.model,
        )


# ---- tests ----

def test_fake_provider_satisfies_protocol():
    """A FakeProvider with name+model+run_loop is structurally a valid LLMProvider."""
    fp = FakeProvider()
    # Don't assert isinstance — Protocol with non-runtime_checkable requires the dance.
    # Just exercise the surface.
    assert fp.name == "fake"
    assert fp.model == "fake-model"
    assert hasattr(fp, "run_loop")


def test_task_runs_against_fake_provider():
    """The Task machinery runs to completion against any LLMProvider implementation."""
    task = CompanyOverview()
    fp = FakeProvider()
    result = task.run("ACME Corp", provider=fp)
    assert result.error is None, f"unexpected error: {result.error}"
    assert result.task_name == "company_overview"
    assert result.confidence == "high"
    assert result.output is not None
    assert result.output["company_name"] == "Test"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.provider_name == "fake"


def test_task_handles_unparseable_output():
    """If the model returns text without valid JSON, Task marks it failed gracefully."""
    task = CompanyOverview()
    fp = FakeProvider(canned_text="I am just plain text with no JSON")
    result = task.run("ACME Corp", provider=fp)
    assert result.confidence == "failed"
    assert result.error is not None
    assert "without JSON" in result.error or "JSON" in result.error


def test_task_handles_provider_error():
    """If the provider returns stop_reason=error, Task surfaces it as failed."""
    @dataclass
    class BrokenProvider:
        name: str = "broken"
        model: str = "broken"
        def run_loop(self, *a, **kw):
            return ProviderResult(
                text="", tool_calls_made=0, input_tokens=0, output_tokens=0,
                iterations=0, stop_reason="error", error="simulated failure",
                model_used=self.model,
            )

    task = CompanyOverview()
    result = task.run("ACME Corp", provider=BrokenProvider())
    assert result.confidence == "failed"
    assert result.error == "simulated failure"
