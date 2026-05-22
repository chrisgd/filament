"""The `run_shell` tool: run a shell command via subprocess."""

from __future__ import annotations

import subprocess

from .base import Tool

_TIMEOUT_SECONDS = 30

# obviously dangerous because you could arbitrarily
# execute any code on the machine running the agent,
# but useful for a starting point. Instead you'd want
# to build more specific tools that only allow certain
# safe or protected operations.
def _run_shell(arguments: dict[str, object]) -> str:
    command = arguments["command"]
    if not isinstance(command, str):
        raise ValueError("run_shell: 'command' must be a string")
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"command timed out after {_TIMEOUT_SECONDS}s: {command}"
        ) from None
    return (
        f"exit code: {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}"
    )


run_shell_tool = Tool(
    name="run_shell",
    description=(
        "Run a shell command via subprocess with a 30-second timeout. Returns "
        "the exit code together with combined stdout and stderr."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            },
        },
        "required": ["command"],
    },
    handler=_run_shell,
)
