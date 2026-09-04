"""Tests for the tool layer: the registry and the registered tools.

These tests run offline; none of them require an LLM.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from filament.tools import Tool, build_registry
from filament.tools import ask_user as ask_user_module
from filament.tools import workdir
from filament.tools.base import Registry


@pytest.fixture(autouse=True)
def workdir_root(tmp_path):
    """Confine every tool test to its own `tmp_path`.

    `set_root` mutates module state, so restore the default (the process
    cwd) afterwards rather than leak one test's root into the next.
    """
    workdir.set_root(tmp_path)
    yield tmp_path
    workdir.set_root(Path.cwd())


# --- Registry --------------------------------------------------------------


def test_registry_tools_lists_registered_tools() -> None:
    registry = build_registry()
    names = {tool.name for tool in registry.tools()}
    assert names == {"read_file", "write_file", "run_shell", "ask_user"}


def test_registry_invoke_dispatches_to_handler() -> None:
    registry = Registry()
    registry.register(
        Tool(
            name="echo",
            description="echo",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: f"got {args['value']}",
        )
    )
    assert registry.invoke("echo", {"value": "hi"}) == "got hi"


def test_registry_invoke_unknown_tool_raises() -> None:
    registry = build_registry()
    with pytest.raises(KeyError):
        registry.invoke("does_not_exist", {})


def test_registry_rejects_duplicate_registration() -> None:
    registry = build_registry()
    with pytest.raises(ValueError):
        registry.register(
            Tool(
                name="read_file",
                description="dup",
                parameters={"type": "object", "properties": {}},
                handler=lambda args: "",
            )
        )


# --- read_file -------------------------------------------------------------


def test_read_file_happy_path(tmp_path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("contents here", encoding="utf-8")
    registry = build_registry()
    assert registry.invoke("read_file", {"path": str(target)}) == "contents here"


def test_read_file_missing_file_raises(tmp_path) -> None:
    registry = build_registry()
    with pytest.raises(FileNotFoundError):
        registry.invoke("read_file", {"path": str(tmp_path / "nope.txt")})


# --- write_file ------------------------------------------------------------


def test_write_file_happy_path(tmp_path) -> None:
    target = tmp_path / "out.txt"
    registry = build_registry()
    result = registry.invoke(
        "write_file", {"path": str(target), "content": "data"}
    )
    assert "out.txt" in result
    assert target.read_text(encoding="utf-8") == "data"


def test_write_file_creates_parent_directories(tmp_path) -> None:
    target = tmp_path / "nested" / "deep" / "out.txt"
    registry = build_registry()
    registry.invoke("write_file", {"path": str(target), "content": "x"})
    assert target.read_text(encoding="utf-8") == "x"


def test_write_file_rejects_non_string_content(tmp_path) -> None:
    registry = build_registry()
    with pytest.raises(ValueError):
        registry.invoke(
            "write_file", {"path": str(tmp_path / "o.txt"), "content": 123}
        )


# --- run_shell -------------------------------------------------------------


def test_run_shell_happy_path() -> None:
    registry = build_registry()
    result = registry.invoke("run_shell", {"command": "echo filament"})
    assert "exit code: 0" in result
    assert "filament" in result


def test_run_shell_reports_nonzero_exit() -> None:
    registry = build_registry()
    result = registry.invoke("run_shell", {"command": "exit 3"})
    assert "exit code: 3" in result


def test_run_shell_stdout_without_trailing_newline() -> None:
    registry = build_registry()
    result = registry.invoke("run_shell", {"command": "printf nonewline"})
    assert "nonewline\n--- stderr ---" in result


def test_run_shell_timeout_raises(monkeypatch) -> None:
    from filament.tools import run_shell as run_shell_module

    monkeypatch.setattr(run_shell_module, "_TIMEOUT_SECONDS", 1)
    registry = build_registry()
    with pytest.raises(TimeoutError, match="timed out"):
        registry.invoke("run_shell", {"command": "sleep 5"})


# --- ask_user --------------------------------------------------------------


@pytest.fixture
def ask_user_streams_reset():
    """Restore ask_user's module-level streams after each test.

    `configure_streams` mutates globals, so tests that don't restore them
    would leak stdin/stdout fakes into unrelated tests in the same run.
    """
    yield
    ask_user_module.configure_streams(sys.stdin, sys.stdout)


def test_ask_user_happy_path(ask_user_streams_reset) -> None:
    stdin = io.StringIO("yes please\n")
    stdout = io.StringIO()
    ask_user_module.configure_streams(stdin, stdout)
    registry = build_registry()
    result = registry.invoke("ask_user", {"question": "go ahead?"})
    assert result == "yes please"
    written = stdout.getvalue()
    assert "[ask_user] go ahead?" in written
    assert "> " in written


def test_ask_user_strips_trailing_newline(ask_user_streams_reset) -> None:
    ask_user_module.configure_streams(io.StringIO("answer\n"), io.StringIO())
    registry = build_registry()
    assert registry.invoke("ask_user", {"question": "?"}) == "answer"


def test_ask_user_strips_crlf(ask_user_streams_reset) -> None:
    # Same reason interactive.py strips \r\n: piped CRLF input on Unix is real.
    ask_user_module.configure_streams(io.StringIO("answer\r\n"), io.StringIO())
    registry = build_registry()
    assert registry.invoke("ask_user", {"question": "?"}) == "answer"


def test_ask_user_empty_response_is_returned_verbatim(
    ask_user_streams_reset,
) -> None:
    # An empty line (just Enter) is a real answer, not EOF.
    ask_user_module.configure_streams(io.StringIO("\n"), io.StringIO())
    registry = build_registry()
    assert registry.invoke("ask_user", {"question": "?"}) == ""


def test_ask_user_eof_raises(ask_user_streams_reset) -> None:
    ask_user_module.configure_streams(io.StringIO(""), io.StringIO())
    registry = build_registry()
    with pytest.raises(EOFError):
        registry.invoke("ask_user", {"question": "anyone there?"})


def test_ask_user_non_string_question_raises(ask_user_streams_reset) -> None:
    ask_user_module.configure_streams(io.StringIO("ignored\n"), io.StringIO())
    registry = build_registry()
    with pytest.raises(ValueError):
        registry.invoke("ask_user", {"question": 42})


def test_ask_user_eof_through_loop_surfaces_as_tool_error(
    ask_user_streams_reset, tmp_path
) -> None:
    """An EOFError in the handler should reach the model as a text result.

    Verifies the spec's end-to-end story: `_dispatch` catches `EOFError` like
    any other tool exception, formats it as `error: EOFError: ...`, and the
    model sees that string in its next `model_call`.
    """
    from filament.agent import run_agent
    from filament.session import Session
    from filament.types import Response, Role, ToolCall
    from tests.fakes import FakeClient

    ask_user_module.configure_streams(io.StringIO(""), io.StringIO())
    client = FakeClient(
        [
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="ask_user", arguments={"question": "x?"})
                ]
            ),
            Response(text="couldn't get an answer"),
        ]
    )
    registry = build_registry()
    with Session(tmp_path / "s.jsonl") as session:
        result = run_agent("ask me", client, registry, session, "fake")
    assert result == "couldn't get an answer"
    second_call_messages = client.calls[1]
    tool_message = second_call_messages[-1]
    assert tool_message.role == Role.TOOL
    assert tool_message.content.startswith("error: EOFError: ")


# --- workdir ---------------------------------------------------------------


def test_workdir_relative_path_resolves_under_root(tmp_path) -> None:
    resolved = workdir.resolve_within("notes.txt", "t")
    assert resolved == tmp_path.resolve() / "notes.txt"


def test_workdir_absolute_path_inside_root_is_accepted(tmp_path) -> None:
    inside = tmp_path / "sub" / "f.txt"
    assert workdir.resolve_within(str(inside), "t") == inside.resolve()


def test_workdir_absolute_path_outside_root_is_refused() -> None:
    with pytest.raises(PermissionError, match="escapes the working directory"):
        workdir.resolve_within("/etc/passwd", "t")


def test_workdir_dotdot_traversal_is_refused() -> None:
    with pytest.raises(PermissionError):
        workdir.resolve_within("../outside.txt", "t")


def test_workdir_symlink_pointing_outside_is_refused(
    tmp_path, tmp_path_factory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(PermissionError):
        workdir.resolve_within("link/secret.txt", "t")


def test_workdir_tilde_is_expanded_before_check(
    tmp_path_factory, monkeypatch
) -> None:
    # With HOME outside the root, ~/x must be refused, not created as a
    # literal "~" directory under the root.
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    with pytest.raises(PermissionError, match="escapes"):
        workdir.resolve_within("~/.ssh/config", "t")


def test_workdir_set_root_rejects_missing_directory(tmp_path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        workdir.set_root(tmp_path / "nope")


def test_workdir_root_defaults_to_cwd(monkeypatch) -> None:
    monkeypatch.setattr(workdir, "_root", None)
    assert workdir.root() == Path.cwd().resolve()


# --- working-directory boundary through the tools --------------------------


def test_read_file_outside_working_directory_is_refused(
    tmp_path_factory,
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    registry = build_registry()
    with pytest.raises(PermissionError, match="escapes the working directory"):
        registry.invoke("read_file", {"path": str(outside)})


def test_read_file_relative_path_resolves_from_root_not_cwd(tmp_path) -> None:
    # The root is tmp_path (autouse fixture); the process cwd is elsewhere.
    (tmp_path / "hello.txt").write_text("from root", encoding="utf-8")
    registry = build_registry()
    assert registry.invoke("read_file", {"path": "hello.txt"}) == "from root"


def test_write_file_outside_working_directory_is_refused(
    tmp_path_factory,
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "evil.txt"
    registry = build_registry()
    with pytest.raises(PermissionError):
        registry.invoke("write_file", {"path": str(outside), "content": "x"})
    assert not outside.exists()


def test_write_file_relative_path_lands_under_root(tmp_path) -> None:
    registry = build_registry()
    registry.invoke("write_file", {"path": "out/notes.txt", "content": "hi"})
    assert (tmp_path / "out" / "notes.txt").read_text(encoding="utf-8") == "hi"


def test_run_shell_runs_in_working_directory(tmp_path) -> None:
    registry = build_registry()
    result = registry.invoke("run_shell", {"command": "pwd"})
    assert str(tmp_path.resolve()) in result
