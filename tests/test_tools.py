"""Tests for the tool layer: the registry and the three initial tools.

These tests run offline; none of them require an LLM.
"""

from __future__ import annotations

import pytest

from filament.tools import Tool, build_registry
from filament.tools.base import Registry


# --- Registry --------------------------------------------------------------


def test_registry_schemas_lists_registered_tools() -> None:
    registry = build_registry()
    names = {tool.name for tool in registry.schemas()}
    assert names == {"read_file", "write_file", "run_shell"}


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
