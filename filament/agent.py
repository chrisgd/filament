"""The agent loop: read, decide, act, observe.

This module owns control flow and nothing else. It speaks only Filament's
internal types, the `ModelClient` interface, and the registry interface. It
contains no tool-specific logic and no backend-specific logic — if either
appears here, something has gone wrong (see CLAUDE.md).
"""

from __future__ import annotations

from .model_clients.base import ModelClient
from .session import Session
from .tools.base import Registry
from .types import Message

SYSTEM_PROMPT = (
    "Use the provided tools to accomplish the user's task. "
    "Read state before changing it. "
    "When the task is complete, respond with a plain-text answer and stop calling tools."
)

# Alternative, more robust system prompt, kept as an instructional example.
# SYSTEM_PROMPT = (
#   "Accomplish the user's task using the provided tools. "
#   "Read before you write: inspect relevant files, run commands to check state, "
#   "and confirm assumptions before making changes. "
#   "Stay within the scope of what was asked. If a request is ambiguous, ask "
#   "rather than guess. If a tool errors, read the error and adjust — do not "
#   "retry the same call. "
#   "You are done when the user's task is complete. Respond with a plain-text "
#   "answer summarizing what you did, and make no further tool calls."
# )

MAX_ITERATIONS = 25


class Conversation:
    """A multi-turn conversation against the agent loop.

    Owns the accumulating `messages` list, the model client, the registry, the
    session, and the backend label. Calling `.send(task)` appends a user turn,
    runs the read/decide/act/observe loop until a final text or
    `MAX_ITERATIONS`, and returns the text. State persists across calls so
    follow-up tasks see the prior turns.

    `MAX_ITERATIONS` is a per-`send()` cap, not a per-conversation cap: one
    user task gets one bounded reasoning budget.

    The one-shot `run_agent` below is a thin wrapper that constructs a
    Conversation and calls `.send` once. See @specs/SPEC-interactive.md.
    """

    def __init__(
        self,
        client: ModelClient,
        registry: Registry,
        session: Session,
        backend: str,
    ) -> None:
        self._client = client
        self._registry = registry
        self._session = session
        self._backend = backend
        self.messages: list[Message] = [
            Message(role="system", content=SYSTEM_PROMPT),
        ]

    def send(self, task: str) -> str:
        """Append the user task, run the loop, return the final text."""
        self.messages.append(Message(role="user", content=f"Task: {task}"))
        tools = self._registry.tools()

        for _ in range(MAX_ITERATIONS):
            self._session.log_model_call(self._backend, self.messages)
            response = self._client.complete(self.messages, tools)
            self._session.log_model_response(response)

            if response.final_text is not None:
                # Record the assistant's final answer in conversation history
                # so the next .send() sees it. For one-shot use this is
                # invisible — the messages list is discarded on return.
                self.messages.append(
                    Message(role="assistant", content=response.final_text)
                )
                return response.final_text

            if not response.tool_calls:
                return "Stopped: model returned empty output."

            self.messages.append(
                Message(
                    role="assistant",
                    content=None,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                self._session.log_tool_call(call)
                result = _dispatch(self._registry, call.name, call.arguments)
                self._session.log_tool_result(call.id, call.name, result)
                self.messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        return (
            f"Stopped: reached the {MAX_ITERATIONS}-iteration limit without a "
            "final answer."
        )


def run_agent(
    task: str,
    client: ModelClient,
    registry: Registry,
    session: Session,
    backend: str,
) -> str:
    """Run the agent loop on a single task and return the final text.

    A thin wrapper around `Conversation.send()` preserved for one-shot CLI
    use and for tests. For multi-turn use, construct a `Conversation`
    directly. `backend` is recorded in the transcript; the loop itself never
    branches on it.
    """
    return Conversation(client, registry, session, backend).send(task)


def _dispatch(
    registry: Registry, name: str, arguments: dict[str, object]
) -> str:
    """Invoke a tool through the registry, turning failures into text.

    A tool failure is observation, not a crash: the error string is fed back to
    the model so it can adjust. This is generic — not tool-specific logic.
    """
    try:
        return registry.invoke(name, arguments)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as text
        return f"error: {type(exc).__name__}: {exc}"
