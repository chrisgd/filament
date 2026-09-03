"""Tests for filament/interactive.py — the multi-turn read-loop.

Drives the loop offline by injecting `io.StringIO` for stdin / stdout / stderr
and a scripted `FakeClient` for the model. No live LLM, no terminal needed.
"""

from __future__ import annotations

import io

import httpx

from filament.interactive import ConsoleReporter, run_interactive
from filament.model_clients.base import ModelResponseError
from filament.session import Session
from filament.tools.base import Registry, Tool
from filament.types import Message, Response, ToolCall


class FakeClient:
    """Replays a scripted list of Responses. Same pattern as test_agent.py."""

    def __init__(self, scripted: list[Response]) -> None:
        self._scripted = list(scripted)
        self.calls: list[list[Message]] = []

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        self.calls.append(list(messages))
        return self._scripted.pop(0)


def _run(
    tmp_path, client, script: str
) -> tuple[int, str, str]:
    """Run the interactive loop with `script` as stdin; return (code, out, err)."""
    stdin = io.StringIO(script)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with Session(tmp_path / "s.jsonl") as session:
        code = run_interactive(
            client,
            Registry(),
            session,
            "fake",
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    return code, stdout.getvalue(), stderr.getvalue()


def test_two_turn_conversation_then_exit(tmp_path) -> None:
    client = FakeClient(
        [
            Response(text="answer one"),
            Response(text="answer two"),
        ]
    )
    code, out, _ = _run(
        tmp_path, client, "first task\nsecond task\n/exit\n"
    )
    assert code == 0
    assert "interactive mode" in out
    assert "answer one" in out
    assert "answer two" in out
    # The second turn's model_call must see the first turn's history.
    second_call = client.calls[1]
    roles = [m.role for m in second_call]
    assert roles == ["system", "user", "assistant", "user"]


def test_eof_exits_cleanly_without_model_call(tmp_path) -> None:
    client = FakeClient([])
    code, _, _ = _run(tmp_path, client, "")
    assert code == 0
    assert client.calls == []


def test_empty_lines_reprompt_without_model_call(tmp_path) -> None:
    client = FakeClient([Response(text="ok")])
    code, _, _ = _run(
        tmp_path, client, "\n   \nactual task\n/exit\n"
    )
    assert code == 0
    assert len(client.calls) == 1


def test_reset_clears_history(tmp_path) -> None:
    client = FakeClient(
        [Response(text="ans one"), Response(text="ans two")]
    )
    _, out, _ = _run(
        tmp_path, client, "first\n/reset\nsecond\n/exit\n"
    )
    assert "conversation reset" in out
    # The second model_call (post-reset) must NOT see the first turn.
    second_call = client.calls[1]
    roles = [m.role for m in second_call]
    assert roles == ["system", "user"]
    assert second_call[1].content == "Task: second"


def test_messages_command_prints_breakdown(tmp_path) -> None:
    client = FakeClient([Response(text="ok")])
    _, out, _ = _run(tmp_path, client, "a task\n/messages\n/exit\n")
    # After one turn: system(1) + user(1) + assistant(1) = 3 messages.
    assert "3 messages" in out
    assert "1 system" in out
    assert "1 user" in out
    assert "1 assistant" in out


def test_help_command_prints_command_list(tmp_path) -> None:
    client = FakeClient([])
    _, out, _ = _run(tmp_path, client, "/help\n/exit\n")
    assert "/exit" in out
    assert "/reset" in out
    assert "/messages" in out
    assert client.calls == []


def test_unknown_slash_command_is_treated_as_task(tmp_path) -> None:
    client = FakeClient([Response(text="examined the file")])
    _, _, _ = _run(
        tmp_path, client, "/etc/hosts has a typo\n/exit\n"
    )
    assert len(client.calls) == 1
    user_message = client.calls[0][1]
    assert user_message.role == "user"
    assert "/etc/hosts" in (user_message.content or "")


def test_crlf_slash_commands_are_recognized(tmp_path) -> None:
    # Issue 13: piped input with CRLF line endings (e.g. from a Windows-edited
    # file or `printf "/exit\r\n"`) must still match the exact-command set.
    # Without the \r strip, /exit becomes /exit\r and is sent to the model.
    client = FakeClient([Response(text="ok")])
    code, out, _ = _run(
        tmp_path, client, "first task\r\n/exit\r\n"
    )
    assert code == 0
    # The task ran (one model call), and /exit was honored — no second call.
    assert len(client.calls) == 1
    user_message = client.calls[0][1]
    assert user_message.content == "Task: first task"


class FailThenSucceed:
    """Raises `error` on the first model call, then returns a final text."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        self.call_count += 1
        if self.call_count == 1:
            raise self._error
        return Response(text="recovered")


def test_httpx_error_inside_a_turn_does_not_kill_the_loop(tmp_path) -> None:
    client = FailThenSucceed(httpx.ConnectError("connection refused"))
    code, out, err = _run(
        tmp_path, client, "first try\nsecond try\n/exit\n"
    )
    assert code == 0
    assert "error:" in err
    assert "ConnectError" in err
    assert "recovered" in out
    assert client.call_count == 2


def test_model_response_error_inside_a_turn_does_not_kill_the_loop(
    tmp_path,
) -> None:
    # Issue 14: a reply the client can't translate (e.g. malformed tool-call
    # JSON from an open-weights model) must not take the session down.
    client = FailThenSucceed(ModelResponseError("malformed tool-call arguments"))
    code, out, err = _run(
        tmp_path, client, "first try\nsecond try\n/exit\n"
    )
    assert code == 0
    assert "error: ModelResponseError: malformed tool-call arguments" in err
    assert "recovered" in out
    assert client.call_count == 2


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


def _run_with_registry(
    tmp_path, client, registry: Registry, script: str
) -> tuple[int, str, str]:
    """Like `_run` but with a caller-supplied registry, for tool-path tests."""
    stdin = io.StringIO(script)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with Session(tmp_path / "s.jsonl") as session:
        code = run_interactive(
            client,
            registry,
            session,
            "fake",
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    return code, stdout.getvalue(), stderr.getvalue()


def test_interactive_emits_activity_signals_around_turn(tmp_path) -> None:
    # A turn that dispatches one tool then finishes should produce:
    # [thinking...], [tool] read_file path="README.md", [tool ok] ..., final.
    client = FakeClient(
        [
            Response(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ]
            ),
            Response(text="summary text"),
        ]
    )
    registry = _registry_with("read_file", lambda args: "FILE CONTENT")
    _, out, _ = _run_with_registry(
        tmp_path, client, registry, "read README\n/exit\n"
    )
    # Two model calls → two [thinking...] lines.
    assert out.count("[thinking...]") == 2
    assert '[tool] read_file path="README.md"' in out
    # len("FILE CONTENT") == 12
    assert "[tool ok] read_file (12 bytes)" in out
    # The model's final text still prints below the activity signals.
    assert "summary text" in out
    # Ordering: [tool] line appears before [tool ok], both before final text.
    tool_idx = out.index("[tool] read_file")
    ok_idx = out.index("[tool ok] read_file")
    final_idx = out.index("summary text")
    assert tool_idx < ok_idx < final_idx


def test_interactive_renders_tool_error_with_error_type(tmp_path) -> None:
    def boom(args: dict) -> str:
        raise FileNotFoundError("nope.txt")

    client = FakeClient(
        [
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="bad", arguments={})
                ]
            ),
            Response(text="recovered"),
        ]
    )
    registry = _registry_with("bad", boom)
    _, out, _ = _run_with_registry(
        tmp_path, client, registry, "try it\n/exit\n"
    )
    assert "[tool err] bad: FileNotFoundError" in out
    # Compact: the full error message stays in the transcript and the next
    # model context but is not echoed verbatim on the activity line.
    assert "nope.txt" not in out


def test_console_reporter_truncates_long_argument_values() -> None:
    # Argument values longer than ~60 chars get a "..." trailer in the echoed
    # line. The full value reaches the transcript and the model unchanged.
    out = io.StringIO()
    reporter = ConsoleReporter(out)
    long_value = "x" * 200
    reporter.tool_call("run_shell", {"cmd": long_value})
    line = out.getvalue()
    assert "run_shell" in line
    assert "..." in line
    # The full 200-x value must not appear verbatim.
    assert long_value not in line


def test_console_reporter_handles_empty_tool_result() -> None:
    # Empty tool output is a real, common result (e.g. write_file). The
    # reporter renders it as `(0 bytes)` rather than skipping the line.
    out = io.StringIO()
    reporter = ConsoleReporter(out)
    reporter.tool_result("write_file", "")
    assert "[tool ok] write_file (0 bytes)" in out.getvalue()


def test_console_reporter_handles_tool_call_with_no_arguments() -> None:
    # No-argument tool calls should not leave a trailing space on the line.
    out = io.StringIO()
    reporter = ConsoleReporter(out)
    reporter.tool_call("ping", {})
    line = out.getvalue()
    assert line == "[tool] ping\n"


def test_http_status_error_inside_a_turn_prints_response_body(tmp_path) -> None:
    # Issue 15: same as the one-shot path; the body reaches stderr and the
    # loop continues.
    request = httpx.Request("POST", "https://backend.example/v1/messages")
    response = httpx.Response(
        400,
        text='{"error":{"message":"max_tokens too large"}}',
        request=request,
    )
    client = FailThenSucceed(
        httpx.HTTPStatusError(
            "Client error '400'", request=request, response=response
        )
    )
    code, out, err = _run(
        tmp_path, client, "first try\nsecond try\n/exit\n"
    )
    assert code == 0
    assert "error: HTTPStatusError" in err
    assert "max_tokens too large" in err
    assert "recovered" in out
