"""Filament's internal types.

The agent loop, tool registry, and session module operate exclusively on these
types. Backend-specific wire formats are hidden by the model clients and
never appear outside of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Who produced a `Message`. Filament's vocabulary, not either backend's.

    A `StrEnum`: a member compares equal to, hashes like, and serializes as
    its string, so `Role.USER` is `"user"` on the wire and in the transcript
    with no `.value` needed. Both wire formats use these same strings for
    the roles they have; where a backend lacks one, its client translates
    (the Anthropic client lifts SYSTEM into a request parameter and sends
    TOOL as a `tool_result` block inside a user turn).

    Members:
        SYSTEM: The harness's standing instructions; always the first message.
        USER: The task, from the human.
        ASSISTANT: The model's turn: its text, its tool calls, or both.
        TOOL: A tool's result, fed back to the model as observation.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A request from the model to invoke a tool.

    Attributes:
        id: The backend's identifier for this call. The tool-role `Message`
            that answers it carries the same value as `tool_call_id`; that
            is how the backend pairs a result with its request.
        name: The tool to invoke, as registered in the `Registry`.
        arguments: The tool's input as an already-parsed dict, never a JSON
            string. Handed to the tool's handler as-is.
    """

    id: str
    name: str
    arguments: dict[str, object]


@dataclass
class Message:
    """One turn in the conversation history.

    Attributes:
        role: Who produced the turn. See `Role`.
        content: The turn's text: the system prompt, the user's task, what
            the model said, or a tool's result. `None` on an assistant turn
            that carried only tool calls.
        tool_calls: Assistant turns only. The calls the model asked for;
            `None` when it made none.
        tool_call_id: Tool turns only. The `ToolCall.id` this result answers.
        name: Tool turns only. The name of the tool that ran.
        provider_state: Assistant turns only. Backend-private state copied
            from the `Response` that produced the turn, so the client that
            produced it can send it back on the next request. See `Response`.
    """

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    provider_state: object | None = None

    def __post_init__(self) -> None:
        # Accept the plain string too, so `Message(role="user")` works, and
        # reject anything outside the vocabulary here rather than let a
        # malformed turn reach a backend.
        self.role = Role(self.role)


@dataclass
class Response:
    """What a model client returns from `complete`.

    Attributes:
        text: What the model said this turn, or `None` if it said nothing.
        tool_calls: What the model asked the harness to do; empty if nothing.
        truncated: The backend cut the output off at its token limit, so
            whatever came back is incomplete. Clients carry only the text of
            such a turn, and the loop stops rather than act on it.
        provider_state: Opaque, JSON-serializable state that only the client
            which produced it may interpret. Today it holds the Anthropic
            client's thinking blocks, which the Messages API requires back
            unchanged on the next request. It is typed `object` on purpose:
            a typed shape would put one backend's wire format into this
            module, which exists to keep that out. The loop copies it onto
            the assistant message it builds and never reads it. See
            @specs/SPEC-provider-state.md.

    `text` and `tool_calls` may both be present: models often say "let me
    read the file first" alongside the call, and both wire formats carry
    that. A turn with tool calls is not final, whatever its text. A turn
    with neither is genuinely empty output, which the agent loop surfaces
    explicitly rather than mistaking for an answer.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    provider_state: object | None = None
