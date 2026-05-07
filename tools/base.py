"""Tool protocol — abstracts over web search, hiring signals, etc.

Tools are provider-neutral. Each provider adapter translates this generic
schema into its own tool-use wire format.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """A callable tool the agent can invoke. Provider-neutral."""

    name: str                       # short identifier — must match LLM tool spec
    description: str                # natural-language description for the model
    input_schema: dict[str, Any]    # JSON Schema for the tool's input

    def __call__(self, **kwargs: Any) -> str:
        """Execute the tool. Returns text to feed back to the model."""
        ...

    @property
    def call_count(self) -> int:
        """How many times this instance has been called (cost tracking)."""
        ...
