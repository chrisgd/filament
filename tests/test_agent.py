"""Tests for the agent loop.

The loop is exercised with a fake `ModelClient` returning canned `Response`
objects, so these tests run offline with no live LLM.
"""

from __future__ import annotations

import json

import pytest

from filament.agent import MAX_ITERATIONS, run_agent
from filament.session import Session
from filament.tools.base import Registry, Tool
from filament.types import Message, Response, ToolCall


class FakeClient:
    """A ModelClient that replays a scripted list of Responses."""

    def __init__(self, scripted: list[Response]) -> None:
        self._scripted = list(scripted)
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.calls.append(list(messages))
        return self._scripted.pop(0)


def _registry_with(name: str, handler) -> Registry:
    registry = Registry()
    registry.register(
        Tool(
            name=name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    return registry


def test_returns_final_text_without_tool_calls(tmp_path) -> None:
    client = FakeClient([Response(final_text="all done")])
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent(
            "do nothing", client, Registry(), session, "fake"
        )
    assert result == "all done"
    assert len(client.calls) == 1


def test_loop_emits_system_message_before_task(tmp_path) -> None:
    client = FakeClient([Response(final_text="done")])
    with Session(tmp_path / "s.jsonl") as session:
        run_agent("the task", client, Registry(), session, "fake")
    first_call = client.calls[0]
    assert first_call[0].role == "system"
    assert first_call[0].content
    assert first_call[1].role == "user"
    assert first_call[1].content == "Task: the task"


def test_dispatches_tool_call_then_returns_final(tmp_path) -> None:
    client = FakeClient(
        [
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="ping", arguments={})
                ]
            ),
            Response(final_text="finished"),
        ]
    )
    registry = _registry_with("ping", lambda args: "pong")
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("ping it", client, registry, session, "fake")
    assert result == "finished"
    # Second model call should include the assistant + tool messages.
    second_call = client.calls[1]
    assert second_call[-1].role == "tool"
    assert second_call[-1].content == "pong"
    assert second_call[-2].role == "assistant"


def test_tool_failure_is_fed_back_as_text(tmp_path) -> None:
    def boom(args: dict) -> str:
        raise FileNotFoundError("missing.txt")

    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="bad", arguments={})]
            ),
            Response(final_text="recovered"),
        ]
    )
    registry = _registry_with("bad", boom)
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("try it", client, registry, session, "fake")
    assert result == "recovered"
    tool_message = client.calls[1][-1]
    assert tool_message.role == "tool"
    assert "FileNotFoundError" in (tool_message.content or "")


def test_unknown_tool_is_fed_back_as_text(tmp_path) -> None:
    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="ghost", arguments={})]
            ),
            Response(final_text="ok"),
        ]
    )
    with Session(tmp_path / "s.jsonl") as session:
        run_agent("call a ghost", client, Registry(), session, "fake")
    tool_message = client.calls[1][-1]
    assert "error" in (tool_message.content or "")


def test_empty_model_output_stops_with_explicit_message(tmp_path) -> None:
    # A turn with neither final_text nor tool_calls is genuinely empty output.
    client = FakeClient([Response()])
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("do nothing", client, Registry(), session, "fake")
    assert result == "Stopped: model returned empty output."
    assert len(client.calls) == 1


def test_response_rejects_both_fields() -> None:
    with pytest.raises(ValueError):
        Response(
            final_text="done",
            tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
        )


def test_iteration_cap_stops_the_loop(tmp_path) -> None:
    # Always asks for a tool, never finishes.
    looping = [
        Response(tool_calls=[ToolCall(id="c", name="ping", arguments={})])
        for _ in range(MAX_ITERATIONS + 5)
    ]
    client = FakeClient(looping)
    registry = _registry_with("ping", lambda args: "pong")
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("loop forever", client, registry, session, "fake")
    assert "limit" in result
    assert len(client.calls) == MAX_ITERATIONS


def test_transcript_records_every_event(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="ping", arguments={})]
            ),
            Response(final_text="done"),
        ]
    )
    registry = _registry_with("ping", lambda args: "pong")
    with Session(path) as session:
        run_agent("ping", client, registry, session, "rosie")
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    kinds = [e["event"] for e in events]
    assert kinds == [
        "model_call",
        "model_response",
        "tool_call",
        "tool_result",
        "model_call",
        "model_response",
    ]
    assert events[0]["backend"] == "rosie"
