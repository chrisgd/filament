"""Transcript logging.

A session writes a structured, line-per-event JSONL transcript. Events are
typed (`model_call`, `model_response`, `tool_call`, `tool_result`) so faculty
can grep and parse a run without reading prose. The agent loop calls into this
module; it owns no control flow.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from .types import Message, Response, ToolCall

SESSIONS_DIR = Path("filament-sessions")


class Session:
    """Append-only JSONL transcript writer for one agent run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")

    def _write(self, event: str, payload: dict[str, object]) -> None:
        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self._handle.write(json.dumps(record) + "\n")
        self._handle.flush()

    def log_model_call(self, backend: str, messages: list[Message]) -> None:
        """Record a request to the model: the backend and full message list."""
        self._write(
            "model_call",
            {
                "backend": backend,
                "messages": [dataclasses.asdict(m) for m in messages],
            },
        )

    def log_model_response(self, response: Response) -> None:
        """Record the model's response."""
        self._write("model_response", {"response": dataclasses.asdict(response)})

    def log_tool_call(self, tool_call: ToolCall) -> None:
        """Record a tool invocation requested by the model."""
        self._write("tool_call", {"tool_call": dataclasses.asdict(tool_call)})

    def log_tool_result(
        self, tool_call_id: str, name: str, result: str
    ) -> None:
        """Record the result of a dispatched tool call."""
        self._write(
            "tool_result",
            {"tool_call_id": tool_call_id, "name": name, "result": result},
        )

    def log_reset(self) -> None:
        """Record a /reset in an interactive conversation.

        The transcript stays continuous across a reset; this event marks the
        boundary so a later reader can tell that the next model_call's
        message list was deliberately rewound rather than truncated by a bug.
        """
        self._write("conversation_reset", {})

    def close(self) -> None:
        """Close the underlying file handle."""
        self._handle.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def new_session(directory: Path = SESSIONS_DIR) -> Session:
    """Create a session backed by a timestamped file under `directory`."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return Session(directory / f"{stamp}.jsonl")
