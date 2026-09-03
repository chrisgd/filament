"""Filament's internal types.

The agent loop, tool registry, and session module operate exclusively on these
types. Backend-specific wire formats are hidden by the model clients and
never appear outside of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """A request from the model to invoke a tool.

    `arguments` is an already-parsed dict, not a JSON string.
    """

    id: str
    name: str
    arguments: dict[str, object]


@dataclass
class Message:
    """One turn in the conversation history.

    `tool_calls` is only meaningful for assistant messages. `tool_call_id` and
    `name` are only meaningful for tool-role messages.
    """

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class Response:
    """What a model client returns from `complete`.

    `text` is what the model said this turn; `tool_calls` is what it asked
    the harness to do. Both may be present: models often say "let me read
    the file first" alongside the call, and both wire formats carry that. A
    turn with tool calls is not final, whatever its text. A turn with neither
    is genuinely empty output, which the agent loop surfaces explicitly
    rather than mistaking for an answer.

    `truncated` means the backend cut the output off at its token limit, so
    whatever came back is incomplete. Clients carry only the text of such a
    turn, and the loop stops rather than act on it.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
