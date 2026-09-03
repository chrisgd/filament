"""Tests for the command-line entry point.

`main()` is driven through its `argv` parameter with the model client, the
session, and the agent loop stubbed out, so these tests run offline with no
live LLM and no transcript files written to disk.
"""

from __future__ import annotations

import httpx

from filament import cli
from filament.config import Config
from filament.model_clients import ModelResponseError


class FakeSession:
    """Stands in for a Session: records only whether it was closed."""

    def __init__(self) -> None:
        self.path = "fake-session.jsonl"
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _stub_runtime(monkeypatch, *, run_agent) -> FakeSession:
    """Stub everything `main()` reaches past argument parsing.

    `build_client` and the session become inert fakes; `run_agent` is whatever
    behavior the test wants to exercise. Returns the fake session so the test
    can assert it was closed.
    """
    session = FakeSession()
    monkeypatch.setattr(cli, "build_client", lambda config: object())
    monkeypatch.setattr(cli, "new_session", lambda: session)
    monkeypatch.setattr(cli, "run_agent", run_agent)
    return session


def test_no_arguments_starts_interactive_mode(monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_run_interactive(client, registry, session, backend) -> int:
        called["backend"] = backend
        return 0

    session = FakeSession()
    monkeypatch.setattr(cli, "build_client", lambda config: object())
    monkeypatch.setattr(cli, "new_session", lambda: session)
    monkeypatch.setattr(cli, "run_interactive", fake_run_interactive)

    code = cli.main([])
    assert code == 0
    assert "backend" in called  # interactive was actually dispatched to
    assert session.closed


def test_interactive_exit_code_propagates(monkeypatch) -> None:
    monkeypatch.setattr(cli, "build_client", lambda config: object())
    monkeypatch.setattr(cli, "new_session", lambda: FakeSession())
    monkeypatch.setattr(cli, "run_interactive", lambda *args: 7)
    assert cli.main([]) == 7


def test_missing_configuration_is_reported_cleanly(monkeypatch, capsys) -> None:
    def boom(config: Config) -> object:
        raise ValueError("AnthropicClient requires FILAMENT_ANTHROPIC_API_KEY")

    monkeypatch.setattr(cli, "build_client", boom)
    code = cli.main(["read the readme"])
    assert code == 2
    assert "configuration error" in capsys.readouterr().err


def test_happy_path_prints_result_and_closes_session(
    monkeypatch, capsys
) -> None:
    session = _stub_runtime(monkeypatch, run_agent=lambda *args: "the answer")
    code = cli.main(["do a task"])
    assert code == 0
    assert "the answer" in capsys.readouterr().out
    assert session.closed


def test_backend_flag_overrides_config(monkeypatch) -> None:
    base = Config(
        backend="anthropic",
        rosie_endpoint="",
        rosie_model="",
        anthropic_api_key="key",
        anthropic_model="model",
    )
    monkeypatch.setattr(cli, "load_config", lambda: base)
    seen: dict[str, str] = {}

    def fake_run_agent(task, client, registry, session, backend) -> str:
        seen["backend"] = backend
        return "done"

    _stub_runtime(monkeypatch, run_agent=fake_run_agent)
    code = cli.main(["--backend", "rosie", "do a task"])
    assert code == 0
    assert seen["backend"] == "rosie"


def test_backend_failure_reports_clean_error(monkeypatch, capsys) -> None:
    def fail(*args) -> str:
        raise httpx.ConnectError("connection refused")

    _stub_runtime(monkeypatch, run_agent=fail)
    code = cli.main(["do a task"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "ConnectError" in err


def test_session_closed_on_backend_failure(monkeypatch) -> None:
    def fail(*args) -> str:
        raise httpx.ReadTimeout("timed out")

    session = _stub_runtime(monkeypatch, run_agent=fail)
    cli.main(["do a task"])
    assert session.closed


def test_model_response_error_reports_clean_error(monkeypatch, capsys) -> None:
    # Issue 14: a reply the client cannot translate is reported like a
    # transport failure, not as a traceback.
    def fail(*args) -> str:
        raise ModelResponseError("tool call 'read_file' has malformed JSON arguments")

    session = _stub_runtime(monkeypatch, run_agent=fail)
    code = cli.main(["do a task"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error: ModelResponseError: tool call 'read_file'" in err
    assert session.closed
