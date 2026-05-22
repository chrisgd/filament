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


def run_agent(
    task: str,
    client: ModelClient,
    registry: Registry,
    session: Session,
    backend: str,
) -> str:
    """Run the agent loop until the model returns a final answer.

    `backend` is recorded in the transcript; the loop itself never branches on
    it. Returns the model's final text response.
    """
    messages: list[Message] = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=f"Task: {task}"),
    ]
    tools = registry.tools()

    for _ in range(MAX_ITERATIONS):
        session.log_model_call(backend, messages)
        response = client.complete(messages, tools)
        session.log_model_response(response)

        if response.final_text is not None:
            return response.final_text

        if not response.tool_calls:
            return "Stopped: model returned empty output."

        messages.append(
            Message(
                role="assistant",
                content=None,
                tool_calls=response.tool_calls,
            )
        )
        for call in response.tool_calls:
            session.log_tool_call(call)
            result = _dispatch(registry, call.name, call.arguments)
            session.log_tool_result(call.id, call.name, result)
            messages.append(
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
