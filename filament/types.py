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

    `provider_state` is only meaningful for assistant messages: the agent
    loop copies it from the `Response` that produced the turn so the client
    that produced it can send it back on the next request. See `Response`.
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

    `text` is what the model said this turn; `tool_calls` is what it asked
    the harness to do. Both may be present: models often say "let me read
    the file first" alongside the call, and both wire formats carry that. A
    turn with tool calls is not final, whatever its text. A turn with neither
    is genuinely empty output, which the agent loop surfaces explicitly
    rather than mistaking for an answer.

    `truncated` means the backend cut the output off at its token limit, so
    whatever came back is incomplete. Clients carry only the text of such a
    turn, and the loop stops rather than act on it.

    `provider_state` is opaque, JSON-serializable state that only the client
    which produced it may interpret. Today it holds the Anthropic client's
    thinking blocks, which the Messages API requires back unchanged on the
    next request. It is typed `object` on purpose: a typed shape would put
    one backend's wire format into this module, which exists to keep that
    out. The loop copies it onto the assistant message it builds and never
    reads it. See @specs/SPEC-provider-state.md.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    provider_state: object | None = None
