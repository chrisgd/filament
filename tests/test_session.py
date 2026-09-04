"""Tests for the session transcript module. These run offline."""

from __future__ import annotations

import json

from filament.session import Session, new_session
from filament.types import Message, Response, ToolCall


def _read_events(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_session_writes_one_json_object_per_event(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    with Session(path) as session:
        session.log_model_call("rosie", [Message(role="user", content="hi")])
        session.log_model_response(Response(text="done"))
    events = _read_events(path)
    assert [e["event"] for e in events] == ["model_call", "model_response"]
    assert all("timestamp" in e for e in events)


def test_model_call_event_records_backend_and_messages(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    with Session(path) as session:
        session.log_model_call(
            "anthropic", [Message(role="user", content="task")]
        )
    event = _read_events(path)[0]
    assert event["backend"] == "anthropic"
    assert event["messages"][0]["role"] == "user"
    assert event["messages"][0]["content"] == "task"


def test_tool_call_and_result_events(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    with Session(path) as session:
        call = ToolCall(id="c1", name="read_file", arguments={"path": "x"})
        session.log_tool_call(call)
        session.log_tool_result("c1", "read_file", "file contents")
    events = _read_events(path)
    assert events[0]["event"] == "tool_call"
    assert events[0]["tool_call"]["name"] == "read_file"
    assert events[1]["event"] == "tool_result"
    assert events[1]["tool_call_id"] == "c1"
    assert events[1]["result"] == "file contents"


def test_log_reset_writes_conversation_reset_event(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    with Session(path) as session:
        session.log_model_call("fake", [Message(role="user", content="hi")])
        session.log_reset()
        session.log_model_call("fake", [Message(role="user", content="hi")])
    events = _read_events(path)
    kinds = [e["event"] for e in events]
    assert kinds == ["model_call", "conversation_reset", "model_call"]
    assert "timestamp" in events[1]


def test_new_session_creates_timestamped_file(tmp_path) -> None:
    session = new_session(tmp_path)
    session.close()
    assert session.path.parent == tmp_path
    assert session.path.suffix == ".jsonl"
    assert session.path.exists()


def test_provider_state_is_recorded_in_transcript_events(tmp_path) -> None:
    # Backend-private state is a field of Response and of the assistant
    # Message built from it, so it lands in both event types with no session
    # code of its own. It is repeated in every later model_call, which is
    # where that part of the transcript's growth comes from.
    path = tmp_path / "s.jsonl"
    state = {
        "thinking_blocks": [
            {"type": "thinking", "thinking": "", "signature": "sig"}
        ]
    }
    with Session(path) as session:
        session.log_model_response(Response(text="done", provider_state=state))
        session.log_model_call(
            "anthropic",
            [Message(role="assistant", content="done", provider_state=state)],
        )
    events = _read_events(path)
    assert events[0]["response"]["provider_state"] == state
    assert events[1]["messages"][0]["provider_state"] == state
