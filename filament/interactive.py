"""Filament interactive mode: a multi-turn read/print loop over a Conversation.

Owns the read-loop and slash-command parsing. The agent loop itself lives in
`filament/agent.py`; this module is a thin shell that drives a `Conversation`
based on user input. See @specs/SPEC-interactive.md for the full design.
"""

from __future__ import annotations

import sys
from typing import TextIO

import httpx

from .agent import Conversation
from .model_clients.base import ModelClient
from .session import Session
from .tools.base import Registry
from .types import Message

_BANNER = "filament interactive mode. /help for commands, /exit to quit."
_PROMPT = "> "
_HELP_TEXT = (
    "commands:\n"
    "  /exit       quit interactive mode\n"
    "  /reset      drop accumulated turns; keep just the system message\n"
    "  /messages   print message-count breakdown\n"
    "  /help       show this help\n"
    "lines starting with an unknown /command are treated as tasks "
    "(e.g. '/etc/hosts has a typo')."
)
_COMMANDS = frozenset({"/exit", "/reset", "/messages", "/help"})


def run_interactive(
    client: ModelClient,
    registry: Registry,
    session: Session,
    backend: str,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Drive a multi-turn conversation from `stdin`.

    Returns 0 on clean exit (`/exit`, EOF). `httpx` errors from the model
    client are caught, printed to `stderr` as `error: ...`, and the loop
    continues. `KeyboardInterrupt` between turns is caught and the loop
    continues; mid-turn Ctrl-C escapes (the model call is a blocking
    `httpx.post` — documented limitation, see @specs/SPEC-interactive.md).
    """
    conversation = Conversation(client, registry, session, backend)
    print(_BANNER, file=stdout)

    while True:
        try:
            print(_PROMPT, end="", file=stdout, flush=True)
            line = stdin.readline()
        except KeyboardInterrupt:
            print("\ninterrupted.", file=stdout)
            continue

        if line == "":
            # EOF: stdin closed (Ctrl-D, piped input exhausted).
            print("", file=stdout)
            return 0

        line = line.rstrip("\n")
        if not line.strip():
            # Empty line — re-prompt, no model call.
            continue

        # Slash commands are recognized only on exact match. Anything else
        # starting with `/` (e.g. "/etc/hosts has a typo") is a task.
        if line in _COMMANDS:
            if line == "/exit":
                return 0
            if line == "/help":
                print(_HELP_TEXT, file=stdout)
                continue
            if line == "/reset":
                conversation.reset()
                print("conversation reset. 1 message (system).", file=stdout)
                continue
            if line == "/messages":
                print(_message_summary(conversation.messages), file=stdout)
                continue

        try:
            result = conversation.send(line)
        except httpx.HTTPError as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=stderr)
            continue
        print(result, file=stdout)


def _message_summary(messages: list[Message]) -> str:
    """Format the /messages output: count by role."""
    counts = {"system": 0, "user": 0, "assistant": 0, "tool_result": 0}
    for message in messages:
        if message.role == "tool":
            counts["tool_result"] += 1
        elif message.role in counts:
            counts[message.role] += 1
    return (
        f"{len(messages)} messages ("
        f"{counts['system']} system, "
        f"{counts['user']} user, "
        f"{counts['assistant']} assistant, "
        f"{counts['tool_result']} tool_result)"
    )
