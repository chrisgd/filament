"""Tests for filament/interactive.py — the multi-turn read-loop.

Drives the loop offline by injecting `io.StringIO` for stdin / stdout / stderr
and a scripted `FakeClient` for the model. No live LLM, no terminal needed.
"""

from __future__ import annotations

import io

import httpx

from filament.interactive import run_interactive
from filament.session import Session
from filament.tools.base import Registry, Tool
from filament.types import Message, Response


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
            Response(final_text="answer one"),
            Response(final_text="answer two"),
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
    client = FakeClient([Response(final_text="ok")])
    code, _, _ = _run(
        tmp_path, client, "\n   \nactual task\n/exit\n"
    )
    assert code == 0
    assert len(client.calls) == 1


def test_reset_clears_history(tmp_path) -> None:
    client = FakeClient(
        [Response(final_text="ans one"), Response(final_text="ans two")]
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
    client = FakeClient([Response(final_text="ok")])
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
    client = FakeClient([Response(final_text="examined the file")])
    _, _, _ = _run(
        tmp_path, client, "/etc/hosts has a typo\n/exit\n"
    )
    assert len(client.calls) == 1
    user_message = client.calls[0][1]
    assert user_message.role == "user"
    assert "/etc/hosts" in (user_message.content or "")


def test_httpx_error_inside_a_turn_does_not_kill_the_loop(tmp_path) -> None:
    class FailThenSucceed:
        def __init__(self) -> None:
            self.call_count = 0

        def complete(
            self, messages: list[Message], tools: list[Tool]
        ) -> Response:
            self.call_count += 1
            if self.call_count == 1:
                raise httpx.ConnectError("connection refused")
            return Response(final_text="recovered")

    client = FailThenSucceed()
    code, out, err = _run(
        tmp_path, client, "first try\nsecond try\n/exit\n"
    )
    assert code == 0
    assert "error:" in err
    assert "ConnectError" in err
    assert "recovered" in out
    assert client.call_count == 2
