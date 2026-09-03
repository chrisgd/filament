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
    _from_wire_response as _anthropic_from_wire_response,
    _system_prompt,
    _to_wire_messages,
)
from filament.model_clients.base import ModelResponseError
from filament.model_clients.rosie import (
    RosieClient,
    _from_wire_response as _rosie_from_wire_response,
    _to_wire_message,
)
from filament.types import Message, ToolCall


@pytest.mark.integration
def test_rosie_trivial_completion() -> None:
    config = load_config()
    client = RosieClient(config.rosie_endpoint, config.rosie_model)
    response = client.complete(
        [Message(role="user", content="Reply with the single word: filament")],
        [],
    )
    assert response.text is not None
    assert response.text.strip() != ""
    assert response.tool_calls == []


@pytest.mark.integration
def test_anthropic_trivial_completion() -> None:
    config = load_config()
    client = AnthropicClient(config.anthropic_api_key, config.anthropic_model)
    response = client.complete(
        [Message(role="user", content="Reply with the single word: filament")],
        [],
    )
    assert response.text is not None
    assert response.text.strip() != ""
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


def _rosie_tool_call_payload(arguments: str) -> dict:
    """A chat-completions reply carrying one tool call with the given args."""
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_rosie_parses_tool_call_arguments() -> None:
    response = _rosie_from_wire_response(
        _rosie_tool_call_payload('{"path": "README.md"}')
    )
    assert response.text is None
    assert [call.name for call in response.tool_calls] == ["read_file"]
    assert response.tool_calls[0].arguments == {"path": "README.md"}


def test_rosie_malformed_tool_call_json_raises_model_response_error() -> None:
    # Issue 14: open-weights models under vLLM emit truncated argument JSON
    # often enough to matter. That must surface as a Filament error, not as
    # a JSONDecodeError escaping the client.
    with pytest.raises(ModelResponseError, match="malformed JSON"):
        _rosie_from_wire_response(_rosie_tool_call_payload('{"path": "README.md'))


def test_rosie_non_object_tool_call_arguments_raise_model_response_error() -> None:
    with pytest.raises(ModelResponseError, match="JSON object"):
        _rosie_from_wire_response(_rosie_tool_call_payload('["README.md"]'))


def test_anthropic_keeps_text_alongside_tool_use() -> None:
    # Issue 16: "let me read it" followed by the call is the common shape.
    response = _anthropic_from_wire_response(
        {
            "content": [
                {"type": "text", "text": "Let me read it."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
            ],
            "stop_reason": "tool_use",
        }
    )
    assert response.text == "Let me read it."
    assert [call.name for call in response.tool_calls] == ["read_file"]


def test_anthropic_renders_assistant_text_before_tool_use_on_replay() -> None:
    wire = _to_wire_messages(
        [
            Message(
                role="assistant",
                content="Let me read it.",
                tool_calls=[
                    ToolCall(id="t1", name="read_file", arguments={"path": "README.md"})
                ],
            )
        ]
    )
    assert wire == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me read it."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
            ],
        }
    ]


def test_rosie_keeps_content_alongside_tool_calls() -> None:
    payload = _rosie_tool_call_payload('{"path": "README.md"}')
    payload["choices"][0]["message"]["content"] = "Let me read it."
    response = _rosie_from_wire_response(payload)
    assert response.text == "Let me read it."
    assert [call.name for call in response.tool_calls] == ["read_file"]


def test_anthropic_max_tokens_marks_truncated_and_carries_only_text() -> None:
    # Issue 17.
    response = _anthropic_from_wire_response(
        {
            "content": [
                {"type": "text", "text": "Here is the fi"},
                {"type": "tool_use", "id": "t1", "name": "write_file", "input": {}},
            ],
            "stop_reason": "max_tokens",
        }
    )
    assert response.truncated is True
    assert response.text == "Here is the fi"
    assert response.tool_calls == []


def test_anthropic_end_turn_is_not_truncated() -> None:
    response = _anthropic_from_wire_response(
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}
    )
    assert response.truncated is False
    assert response.text == "done"


def test_rosie_length_marks_truncated_without_parsing_tool_calls() -> None:
    # A tool call cut mid-JSON would otherwise raise ModelResponseError; on a
    # truncated reply the client skips parsing and carries only the text.
    payload = _rosie_tool_call_payload('{"path": "README.md')
    payload["choices"][0]["finish_reason"] = "length"
    payload["choices"][0]["message"]["content"] = "Writing the file"
    response = _rosie_from_wire_response(payload)
    assert response.truncated is True
    assert response.text == "Writing the file"
    assert response.tool_calls == []


def test_rosie_stop_is_not_truncated() -> None:
    payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": "done"}}
        ]
    }
    response = _rosie_from_wire_response(payload)
    assert response.truncated is False
    assert response.text == "done"
