"""The agent loop: read, decide, act, observe.

This module owns control flow and nothing else. It speaks only Filament's
internal types, the `ModelClient` interface, and the registry interface. It
contains no tool-specific logic and no backend-specific logic — if either
appears here, something has gone wrong (see CLAUDE.md).
"""

from __future__ import annotations

from typing import Protocol

from .model_clients.base import ModelClient
from .session import Session
from .tools.base import Registry
from .types import Message


class TurnReporter(Protocol):
    """Hook surface for surfacing loop transitions to a human-readable sink.

    A `TurnReporter` is a small contract the agent loop calls at the same
    four control-flow points where `Session.log_*` already fires. Reporters
    own *presentation* (e.g. printing `[thinking...]` to a terminal); the
    structured JSONL transcript remains the canonical record. See
    @specs/SPEC-activity-signals.md.

    Reporters sit inside the loop's trust boundary — exceptions raised here
    propagate out of `Conversation.send()`; there's no error-recovery
    contract as there is for tools.
    """

    def model_call_start(self, iteration: int) -> None: ...
    def model_call_end(self) -> None: ...
    def tool_call(self, name: str, arguments: dict[str, object]) -> None: ...
    def tool_result(self, name: str, result: str) -> None: ...


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
        reporter: TurnReporter | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._session = session
        self._backend = backend
        self._reporter = reporter
        self.messages: list[Message] = [
            Message(role="system", content=SYSTEM_PROMPT),
        ]

    def reset(self) -> None:
        """Drop accumulated turns; keep only the system message.

        Records a `conversation_reset` event in the session so the transcript
        is honest about the boundary between the old and new conversation.
        """
        self.messages = [Message(role="system", content=SYSTEM_PROMPT)]
        self._session.log_reset()

    def send(self, task: str) -> str:
        """Append the user task, run the loop, return the final text."""
        self.messages.append(Message(role="user", content=f"Task: {task}"))
        tools = self._registry.tools()

        for iteration in range(MAX_ITERATIONS):
            self._session.log_model_call(self._backend, self.messages)
            if self._reporter is not None:
                # 1-based iteration is friendlier to humans skimming the log;
                # the loop variable itself remains 0-based.
                self._reporter.model_call_start(iteration + 1)
            response = self._client.complete(self.messages, tools)
            if self._reporter is not None:
                self._reporter.model_call_end()
            self._session.log_model_response(response)

            if response.truncated:
                # The backend cut the output off at its token limit, so what
                # came back is incomplete. Keep any partial text so the user
                # sees it, but stop rather than act on it.
                notice = "Stopped: model output was cut off at the token limit."
                stopped = (
                    f"{response.text}\n\n{notice}" if response.text else notice
                )
                self.messages.append(
                    Message(role="assistant", content=stopped)
                )
                return stopped

            if not response.tool_calls and response.text is not None:
                # Final answer. Record it in conversation history so the
                # next .send() sees it. For one-shot use this is invisible —
                # the messages list is discarded on return.
                self.messages.append(
                    Message(role="assistant", content=response.text)
                )
                return response.text

            if not response.tool_calls:
                # Empty output: record the sentinel as the assistant turn so
                # the conversation history stays coherent for a follow-up
                # .send(). Symmetric with the final-answer path above.
                stopped = "Stopped: model returned empty output."
                self.messages.append(
                    Message(role="assistant", content=stopped)
                )
                return stopped

            # Tool calls. Record the assistant turn with any text the model
            # said alongside its calls ("let me read the README first") so
            # the transcript and the replayed conversation keep it, then
            # dispatch each call and append its result.
            self.messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                self._session.log_tool_call(call)
                if self._reporter is not None:
                    self._reporter.tool_call(call.name, call.arguments)
                result = _dispatch(self._registry, call.name, call.arguments)
                self._session.log_tool_result(call.id, call.name, result)
                if self._reporter is not None:
                    self._reporter.tool_result(call.name, result)
                self.messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        # Iteration cap reached: record the sentinel as the assistant turn so
        # the conversation history stays coherent for a follow-up .send().
        stopped = (
            f"Stopped: reached the {MAX_ITERATIONS}-iteration limit without a "
            "final answer."
        )
        self.messages.append(Message(role="assistant", content=stopped))
        return stopped


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
