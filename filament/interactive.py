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
from .model_clients.base import ModelClient, ModelResponseError
from .session import Session
from .tools import ask_user as ask_user_module
from .tools.base import Registry
from .types import Message, Response

_ARG_VALUE_MAX = 60
_ARG_TRUNCATION_SUFFIX = "..."


class ConsoleReporter:
    """A `TurnReporter` that mirrors loop transitions to a text stream.

    One ASCII line per event, no in-place updates, no color. The point is
    to make the loop visible to a student watching the terminal — see
    @specs/SPEC-activity-signals.md. The structured transcript (JSONL) is
    the canonical record; this is the human-skimmable counterpart.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def model_call_start(self, iteration: int) -> None:
        # The line stays put — the next event (a [tool] line or the model's
        # final text) is itself the signal that the call returned.
        print("[thinking...]", file=self._stream, flush=True)

    def model_call_end(self, response: Response) -> None:
        # Text the model said alongside its tool calls ("let me read the
        # README first") is printed bare, like its final answer: bracketed
        # lines are the harness, bare lines are the model. A final answer
        # is not printed here; the read-loop prints it as the turn's result.
        if response.tool_calls and response.text:
            print(response.text, file=self._stream, flush=True)

    def tool_call(self, name: str, arguments: dict[str, object]) -> None:
        rendered = _format_args(arguments)
        line = f"[tool] {name} {rendered}".rstrip()
        print(line, file=self._stream, flush=True)

    def tool_result(self, name: str, result: str) -> None:
        if result.startswith("error: "):
            # `_dispatch` formats failures as "error: <ErrorType>: <msg>".
            # The reporter surfaces only the type to keep the line compact;
            # the full message is in the transcript and the next model
            # message context. A tool that *returns* a string starting with
            # "error: " would be mis-rendered as a failure — that convention
            # is owned by `_dispatch`, so tool authors shouldn't compose
            # their own "error: " prefix into a success result.
            err_type = result.split(":", 2)[1].strip()
            print(f"[tool err] {name}: {err_type}", file=self._stream, flush=True)
        else:
            print(
                f"[tool ok] {name} ({len(result)} bytes)",
                file=self._stream,
                flush=True,
            )


def _format_args(arguments: dict[str, object]) -> str:
    """Render tool arguments as space-separated key="value" pairs.

    Values are stringified and truncated past ~60 characters so a long
    file body or shell command doesn't blow up the terminal line. The full
    argument value remains in the transcript.
    """
    parts: list[str] = []
    for key, value in arguments.items():
        # Escape line breaks so one event stays one line; the full value is
        # in the transcript. Done before truncating so the width limit is
        # measured on what is printed.
        text = str(value).replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > _ARG_VALUE_MAX:
            text = text[: _ARG_VALUE_MAX - len(_ARG_TRUNCATION_SUFFIX)] + _ARG_TRUNCATION_SUFFIX
        parts.append(f'{key}="{text}"')
    return " ".join(parts)

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

    Returns 0 on clean exit (`/exit`, EOF). `httpx` errors and
    `ModelResponseError` from the model client are caught, printed to
    `stderr` as `error: ...`, and the loop continues. `KeyboardInterrupt`
    between turns is caught and the loop continues; mid-turn Ctrl-C escapes
    (the model call is a blocking `httpx.post` — documented limitation, see
    @specs/SPEC-interactive.md).
    """
    # Redirect the ask_user tool's streams to the same stdin/stdout the
    # read-loop is using. Without this, a mid-turn ask_user call would block
    # on real sys.stdin, which is wrong under piped input and deadlocks tests
    # that drive the loop with io.StringIO. Restored on exit (finally) so a
    # later in-process caller doesn't inherit dangling references to streams
    # that have since been closed.
    ask_user_module.configure_streams(stdin, stdout)
    try:
        reporter = ConsoleReporter(stdout)
        conversation = Conversation(
            client, registry, session, backend, reporter=reporter
        )
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

            line = line.rstrip("\r\n")
            if not line.strip():
                # Empty line — re-prompt, no model call.
                continue

            # Slash commands are recognized only on exact match. Anything
            # else starting with `/` (e.g. "/etc/hosts has a typo") is a task.
            if line in _COMMANDS:
                if line == "/exit":
                    return 0
                if line == "/help":
                    print(_HELP_TEXT, file=stdout)
                    continue
                if line == "/reset":
                    conversation.reset()
                    print(
                        "conversation reset. 1 message (system).", file=stdout
                    )
                    continue
                if line == "/messages":
                    print(_message_summary(conversation.messages), file=stdout)
                    continue

            try:
                result = conversation.send(line)
            except (httpx.HTTPError, ModelResponseError) as exc:
                print(f"error: {type(exc).__name__}: {exc}", file=stderr)
                if isinstance(exc, httpx.HTTPStatusError):
                    # the status line only says e.g. "400 Bad Request"; the
                    # body is where the backend says why
                    body = exc.response.text.strip()
                    if body:
                        print(f"response body: {body}", file=stderr)
                continue
            print(result, file=stdout)
    finally:
        ask_user_module.configure_streams(sys.stdin, sys.stdout)


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
