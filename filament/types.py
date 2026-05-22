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

    A response carries `final_text` (the model is done) or `tool_calls` (the
    model wants to call tools), never both. It may carry neither: that signals
    the model produced genuinely empty output, which the agent loop surfaces
    explicitly rather than mistaking for a final answer.
    """

    final_text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.final_text is not None and self.tool_calls:
            raise ValueError(
                "Response carries both final_text and tool_calls; a response "
                "is final text or tool calls, never both."
            )
