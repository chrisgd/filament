"""Tests for the model clients.

Two kinds of test live here:

- Integration contract tests, marked `@pytest.mark.integration`, hit a real
  backend and are excluded from the default `pytest` run. Run them explicitly
  with `pytest -m integration`. Each sends a trivial completion (one user
  message, no tools) and asserts a coherent text response comes back.
- Offline wire-translation tests exercise the pure translation helpers with no
  network access; they run as part of the default `pytest` run.
"""

from __future__ import annotations

import pytest

from filament.config import load_config
from filament.model_clients.anthropic import (
    AnthropicClient,
    _system_prompt,
    _to_wire_messages,
)
from filament.model_clients.rosie import RosieClient, _to_wire_message
from filament.types import Message


@pytest.mark.integration
def test_rosie_trivial_completion() -> None:
    config = load_config()
    client = RosieClient(config.rosie_endpoint, config.rosie_model)
    response = client.complete(
        [Message(role="user", content="Reply with the single word: filament")],
        [],
    )
    assert response.final_text is not None
    assert response.final_text.strip() != ""
    assert response.tool_calls == []


@pytest.mark.integration
def test_anthropic_trivial_completion() -> None:
    config = load_config()
    client = AnthropicClient(config.anthropic_api_key, config.anthropic_model)
    response = client.complete(
        [Message(role="user", content="Reply with the single word: filament")],
        [],
    )
    assert response.final_text is not None
    assert response.final_text.strip() != ""
    assert response.tool_calls == []


def test_rosie_system_message_passes_through() -> None:
    wire = _to_wire_message(Message(role="system", content="be helpful"))
    assert wire == {"role": "system", "content": "be helpful"}


def test_anthropic_lifts_system_into_top_level_param() -> None:
    messages = [
        Message(role="system", content="be helpful"),
        Message(role="user", content="Task: greet"),
    ]
    assert _system_prompt(messages) == "be helpful"
    wire = _to_wire_messages(messages)
    assert all(m["role"] != "system" for m in wire)
    assert wire == [{"role": "user", "content": "Task: greet"}]


def test_anthropic_system_prompt_empty_without_system_message() -> None:
    messages = [Message(role="user", content="Task: greet")]
    assert _system_prompt(messages) == ""
