"""Tests for the agent loop.

The loop is exercised with a fake `ModelClient` returning canned `Response`
objects, so these tests run offline with no live LLM.
"""

from __future__ import annotations

import json

from filament.agent import MAX_ITERATIONS, Conversation, run_agent
from filament.session import Session
from filament.tools.base import Registry
from filament.types import Response, ToolCall
from tests.fakes import FakeClient, registry_with


def test_returns_text_without_tool_calls(tmp_path) -> None:
    client = FakeClient([Response(text="all done")])
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent(
            "do nothing", client, Registry(), session, "fake"
        )
    assert result == "all done"
    assert len(client.calls) == 1


def test_loop_emits_system_message_before_task(tmp_path) -> None:
    client = FakeClient([Response(text="done")])
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
            Response(text="finished"),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
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
            Response(text="recovered"),
        ]
    )
    registry = registry_with("bad", boom)
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
            Response(text="ok"),
        ]
    )
    with Session(tmp_path / "s.jsonl") as session:
        run_agent("call a ghost", client, Registry(), session, "fake")
    tool_message = client.calls[1][-1]
    assert "error" in (tool_message.content or "")


def test_empty_model_output_stops_with_explicit_message(tmp_path) -> None:
    # A turn with neither text nor tool_calls is genuinely empty output.
    client = FakeClient([Response()])
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("do nothing", client, Registry(), session, "fake")
    assert result == "Stopped: model returned empty output."
    assert len(client.calls) == 1


def test_response_carries_text_alongside_tool_calls() -> None:
    # Issue 16: both wire formats allow text with tool calls, and models use
    # it ("let me read the file first"). The internal type must not drop it.
    response = Response(
        text="Let me look.",
        tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
    )
    assert response.text == "Let me look."
    assert [call.name for call in response.tool_calls] == ["ping"]


def test_interstitial_text_is_kept_on_the_assistant_turn(tmp_path) -> None:
    # Issue 16: the text said alongside a tool call reaches the assistant
    # message, the next model_call, and the transcript.
    path = tmp_path / "s.jsonl"
    client = FakeClient(
        [
            Response(
                text="Let me ping first.",
                tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
            ),
            Response(text="done"),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
    with Session(path) as session:
        run_agent("ping", client, registry, session, "fake")
    assistant_turn = client.calls[1][-2]
    assert assistant_turn.role == "assistant"
    assert assistant_turn.content == "Let me ping first."
    assert [call.name for call in assistant_turn.tool_calls] == ["ping"]
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[1]["event"] == "model_response"
    assert events[1]["response"]["text"] == "Let me ping first."


def test_iteration_cap_stops_the_loop(tmp_path) -> None:
    # Always asks for a tool, never finishes.
    looping = [
        Response(tool_calls=[ToolCall(id="c", name="ping", arguments={})])
        for _ in range(MAX_ITERATIONS + 5)
    ]
    client = FakeClient(looping)
    registry = registry_with("ping", lambda args: "pong")
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
            Response(text="done"),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
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


def test_conversation_accumulates_history_across_turns(tmp_path) -> None:
    client = FakeClient(
        [
            Response(text="answer one"),
            Response(text="answer two"),
        ]
    )
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, Registry(), session, "fake")
        first = conversation.send("first task")
        second = conversation.send("second task")
    assert first == "answer one"
    assert second == "answer two"
    # The second model_call must see the first turn's user + assistant
    # messages, so follow-ups can reference earlier turns.
    second_call = client.calls[1]
    roles = [m.role for m in second_call]
    assert roles == ["system", "user", "assistant", "user"]
    assert second_call[1].content == "Task: first task"
    assert second_call[2].content == "answer one"
    assert second_call[3].content == "Task: second task"


def test_conversation_reset_drops_history_and_starts_fresh(tmp_path) -> None:
    client = FakeClient(
        [
            Response(text="answer one"),
            Response(text="answer two"),
        ]
    )
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, Registry(), session, "fake")
        conversation.send("first task")
        assert len(conversation.messages) == 3
        conversation.reset()
        assert len(conversation.messages) == 1
        assert conversation.messages[0].role == "system"
        conversation.send("after reset")
    # The second model_call (post-reset) must NOT see the first turn.
    second_call = client.calls[1]
    roles = [m.role for m in second_call]
    assert roles == ["system", "user"]
    assert second_call[1].content == "Task: after reset"


def test_conversation_reset_logs_event_to_transcript(tmp_path) -> None:
    import json as _json

    path = tmp_path / "s.jsonl"
    client = FakeClient([Response(text="ok")])
    with Session(path) as session:
        conversation = Conversation(client, Registry(), session, "fake")
        conversation.send("task")
        conversation.reset()
    events = [
        _json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(e["event"] == "conversation_reset" for e in events)


def test_conversation_appends_assistant_on_empty_output(tmp_path) -> None:
    # Issue 12: an empty-output turn must still record an assistant message,
    # otherwise the next .send() produces [system, user, user] — malformed.
    client = FakeClient([Response(), Response(text="recovered")])
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, Registry(), session, "fake")
        first = conversation.send("ask one")
        assert first == "Stopped: model returned empty output."
        assert conversation.messages[-1].role == "assistant"
        assert conversation.messages[-1].content == first
        conversation.send("ask two")
    second_call = client.calls[1]
    roles = [m.role for m in second_call]
    assert roles == ["system", "user", "assistant", "user"]


def test_conversation_appends_assistant_on_iteration_cap(tmp_path) -> None:
    # Issue 12: a turn that hits MAX_ITERATIONS must still record an
    # assistant message before the next .send() appends a user message.
    looping = [
        Response(tool_calls=[ToolCall(id=f"c{i}", name="ping", arguments={})])
        for i in range(MAX_ITERATIONS)
    ]
    client = FakeClient(looping + [Response(text="finally")])
    registry = registry_with("ping", lambda args: "pong")
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, registry, session, "fake")
        first = conversation.send("loop forever")
        assert "limit" in first
        assert conversation.messages[-1].role == "assistant"
        assert conversation.messages[-1].content == first
        conversation.send("after cap")
    # The call after the cap-out must see a coherent end-of-history.
    follow_up_call = client.calls[MAX_ITERATIONS]
    assert follow_up_call[-1].role == "user"
    assert follow_up_call[-2].role == "assistant"


class RecordingReporter:
    """A TurnReporter test double: appends each call to a list of tuples."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def model_call_start(self, iteration: int) -> None:
        self.events.append(("model_call_start", iteration))

    def model_call_end(self, response: Response) -> None:
        self.events.append(("model_call_end", response.text))

    def tool_call(self, name: str, arguments: dict) -> None:
        self.events.append(("tool_call", name, dict(arguments)))

    def tool_result(self, name: str, result: str) -> None:
        self.events.append(("tool_result", name, result))


def test_reporter_fires_at_each_loop_transition(tmp_path) -> None:
    # One tool call then a final text — exercises all four hooks across two
    # iterations: start/end around the first model call, tool_call/tool_result
    # around the dispatch, then start/end around the second model call.
    client = FakeClient(
        [
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="ping", arguments={"x": 1})
                ]
            ),
            Response(text="done"),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
    reporter = RecordingReporter()
    with Session(tmp_path / "s.jsonl") as session:
        Conversation(
            client, registry, session, "fake", reporter=reporter
        ).send("ping it")
    assert reporter.events == [
        ("model_call_start", 1),
        ("model_call_end", None),
        ("tool_call", "ping", {"x": 1}),
        ("tool_result", "ping", "pong"),
        ("model_call_start", 2),
        ("model_call_end", "done"),
    ]


def test_reporter_receives_error_string_for_failed_tools(tmp_path) -> None:
    # The reporter should see the same "error: <Type>: <msg>" string that
    # `_dispatch` returns to the model — formatting is the reporter's job.
    def boom(args: dict) -> str:
        raise FileNotFoundError("missing.txt")

    client = FakeClient(
        [
            Response(tool_calls=[ToolCall(id="c1", name="bad", arguments={})]),
            Response(text="ok"),
        ]
    )
    registry = registry_with("bad", boom)
    reporter = RecordingReporter()
    with Session(tmp_path / "s.jsonl") as session:
        Conversation(
            client, registry, session, "fake", reporter=reporter
        ).send("try it")
    tool_results = [e for e in reporter.events if e[0] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0][1] == "bad"
    assert tool_results[0][2].startswith("error: FileNotFoundError")


def test_omitting_reporter_keeps_loop_silent(tmp_path) -> None:
    # The default behavior (no reporter) must be byte-identical to today —
    # nothing constructs a reporter, nothing fires. This is the regression
    # guard for one-shot mode silence.
    client = FakeClient([Response(text="done")])
    with Session(tmp_path / "s.jsonl") as session:
        # No reporter kwarg passed; .send completes without exceptions.
        Conversation(client, Registry(), session, "fake").send("task")


def test_run_agent_oneshot_emits_no_activity_signals(tmp_path, capsys) -> None:
    # The one-shot wrapper must stay silent: no [thinking...] or [tool] lines
    # leak to stdout/stderr. Activity signals are interactive-only.
    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="ping", arguments={})]
            ),
            Response(text="finished"),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
    with Session(tmp_path / "s.jsonl") as session:
        run_agent("ping", client, registry, session, "fake")
    captured = capsys.readouterr()
    assert "[thinking" not in captured.out
    assert "[tool" not in captured.out
    assert "[thinking" not in captured.err
    assert "[tool" not in captured.err


def test_conversation_messages_grow_across_turns(tmp_path) -> None:
    client = FakeClient(
        [Response(text="a"), Response(text="b")]
    )
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, Registry(), session, "fake")
        assert len(conversation.messages) == 1  # just the system message
        conversation.send("one")
        assert len(conversation.messages) == 3  # +user, +assistant
        conversation.send("two")
        assert len(conversation.messages) == 5  # +user, +assistant


def test_truncated_output_stops_with_partial_text_and_notice(tmp_path) -> None:
    # Issue 17: a reply cut off at the token limit is not a finished answer.
    # The partial text is kept so the user sees it; the notice says why it
    # stopped; the assistant turn is recorded so a follow-up stays coherent.
    path = tmp_path / "s.jsonl"
    client = FakeClient(
        [Response(text="Here is the fi", truncated=True), Response(text="ok")]
    )
    with Session(path) as session:
        conversation = Conversation(client, Registry(), session, "fake")
        result = conversation.send("explain")
        assert result.startswith("Here is the fi")
        assert "cut off at the token limit" in result
        assert conversation.messages[-1].role == "assistant"
        assert conversation.messages[-1].content == result
        conversation.send("go on")
    assert [m.role for m in client.calls[1]] == ["system", "user", "assistant", "user"]
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[1]["response"]["truncated"] is True


def test_truncated_turn_is_never_dispatched(tmp_path) -> None:
    # Even if a client hands over tool calls on a truncated turn, the loop
    # must not act on them.
    dispatched: list[dict] = []
    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
                truncated=True,
            )
        ]
    )
    registry = registry_with("ping", lambda args: dispatched.append(args) or "pong")
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("ping", client, registry, session, "fake")
    assert dispatched == []
    assert result == "Stopped: model output was cut off at the token limit."


def test_provider_state_is_copied_onto_assistant_turns(tmp_path) -> None:
    # Backend-private state (today: Anthropic thinking blocks) rides from the
    # Response onto the assistant message the loop builds, on both the
    # tool-call path and the final-answer path, so the client can replay it
    # on the next request. The loop never reads it. The next model_call and
    # the transcript both carry it. See SPEC-provider-state.md.
    path = tmp_path / "s.jsonl"
    client = FakeClient(
        [
            Response(
                tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
                provider_state={"opaque": 1},
            ),
            Response(text="done", provider_state={"opaque": 2}),
        ]
    )
    registry = registry_with("ping", lambda args: "pong")
    with Session(path) as session:
        conversation = Conversation(client, registry, session, "fake")
        conversation.send("ping")
    tool_turn = client.calls[1][-2]
    assert tool_turn.role == "assistant"
    assert tool_turn.provider_state == {"opaque": 1}
    final_turn = conversation.messages[-1]
    assert final_turn.role == "assistant"
    assert final_turn.content == "done"
    assert final_turn.provider_state == {"opaque": 2}
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[1]["response"]["provider_state"] == {"opaque": 1}
    assert events[4]["event"] == "model_call"
    assert events[4]["messages"][-2]["provider_state"] == {"opaque": 1}


def test_sentinel_turns_carry_no_provider_state(tmp_path) -> None:
    # Empty output, truncation, and the iteration cap append synthetic
    # assistant turns. Those words are the harness's, not the model's, so
    # they carry no provider state even when the Response had some.
    scripted = [
        Response(provider_state={"opaque": 1}),
        Response(text="cut", truncated=True, provider_state={"opaque": 2}),
    ] + [
        Response(
            tool_calls=[ToolCall(id=f"c{i}", name="ping", arguments={})],
            provider_state={"opaque": 3},
        )
        for i in range(MAX_ITERATIONS)
    ]
    client = FakeClient(scripted)
    registry = registry_with("ping", lambda args: "pong")
    with Session(tmp_path / "s.jsonl") as session:
        conversation = Conversation(client, registry, session, "fake")
        for task in ("empty", "truncated", "capped"):
            conversation.send(task)
            assert conversation.messages[-1].role == "assistant"
            assert conversation.messages[-1].provider_state is None
