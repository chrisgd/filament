"""The `ask_user` tool: a human-in-the-loop elicitation channel.

Lets the *agent* drive a single round of Q&A — it calls this tool when it
decides it needs information it doesn't have, and the handler blocks for one
line of input from the user. The user's response is returned as the tool
result, which the agent sees in its next model call.

This is the agent-driven counterpart to interactive mode's user-driven
turn-taking. See @specs/SPEC-ask-user-tool.md.

Stream configuration is module-level state so that interactive mode can
redirect both ends to the same `io.StringIO` (or any other `TextIO`) it
threads through its read-loop. Without this, an `ask_user` invocation
during a test or under piped input would hang on real `sys.stdin`.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .base import Tool

_input_stream: TextIO = sys.stdin
_output_stream: TextIO = sys.stdout


def configure_streams(stdin: TextIO, stdout: TextIO) -> None:
    """Override the streams the `ask_user` handler reads from and writes to.

    Called by `run_interactive` so a mid-turn `ask_user` reads from the same
    stdin the read-loop is using. Tests call this directly with `io.StringIO`.
    One-shot CLI mode never calls it; the `sys.stdin` / `sys.stdout` defaults
    apply.
    """
    global _input_stream, _output_stream
    _input_stream = stdin
    _output_stream = stdout


def _ask_user(arguments: dict[str, object]) -> str:
    question = arguments["question"]
    if not isinstance(question, str):
        raise ValueError("ask_user: 'question' must be a string")
    # Both prints flush so the user sees the question and prompt before
    # readline blocks. Relying on the second print's flush to flush the
    # first would be implicit and would silently break if these were ever
    # split or reordered.
    print(f"[ask_user] {question}", file=_output_stream, flush=True)
    print("> ", end="", file=_output_stream, flush=True)
    line = _input_stream.readline()
    if line == "":
        # `readline` returns "" only on EOF; an empty user response is "\n".
        # Raising here lets `agent._dispatch` surface "error: EOFError: ..."
        # to the model so it can decide what to do (typically: stop and say
        # it couldn't get an answer).
        raise EOFError("ask_user: stdin closed before user responded")
    return line.rstrip("\r\n")


ask_user_tool = Tool(
    name="ask_user",
    description=(
        "Ask the user a clarifying question and wait for their single-line "
        "response. Use this when you need information that isn't available "
        "from the other tools — confirmation, a choice between options, or "
        "missing context. Returns the user's response as a string."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        "required": ["question"],
    },
    handler=_ask_user,
)
