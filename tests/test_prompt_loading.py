"""Verify every prompt module declares VERSION and SYSTEM_PROMPT.

Adds confidence that prompt files imported correctly and aren't drifting from
the contract that tasks expect.
"""

from __future__ import annotations

import importlib
import pkgutil

import prompts


def test_every_prompt_module_has_version_and_system_prompt():
    """Iterate prompts/, import each module, assert it declares VERSION + SYSTEM_PROMPT."""
    for finder, name, ispkg in pkgutil.iter_modules(prompts.__path__):
        # Skip dunder modules + private helpers (e.g. _citations.py supplies the
        # shared citation instructions to every prompt; it's not itself a prompt).
        if name.startswith("__") or name.startswith("_"):
            continue
        module = importlib.import_module(f"prompts.{name}")
        assert hasattr(module, "VERSION"), f"prompts.{name} missing VERSION"
        assert hasattr(module, "SYSTEM_PROMPT"), f"prompts.{name} missing SYSTEM_PROMPT"
        assert isinstance(module.VERSION, str) and module.VERSION.startswith("v")
        assert isinstance(module.SYSTEM_PROMPT, str) and len(module.SYSTEM_PROMPT) > 50
