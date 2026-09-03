"""Rosie model client: OpenAI-compatible chat-completions over httpx.

Rosie serves an open-weights model under vLLM with an OpenAI-compatible API.
This client speaks that wire format directly — visibly, on purpose, for
teaching. Nothing here leaks past `complete`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..tools.base import Tool
from ..types import Message, Response, ToolCall
from .base import ModelResponseError

_TIMEOUT_SECONDS = 120.0


class RosieClient:
    def __init__(self, endpoint: str, model: str) -> None:
        if not endpoint:
            raise ValueError("RosieClient requires FILAMENT_ROSIE_ENDPOINT")
        if not model:
            raise ValueError("RosieClient requires FILAMENT_ROSIE_MODEL")
        self._endpoint = endpoint.rstrip("/")
        self._model = model

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [_to_wire_message(m) for m in messages],
        }
        if tools:
            body["tools"] = [_to_wire_tool(t) for t in tools]
        reply = httpx.post(
            f"{self._endpoint}/chat/completions",
            json=body,
            timeout=_TIMEOUT_SECONDS,
        )
        reply.raise_for_status()
        return _from_wire_response(reply.json())


def _to_wire_message(message: Message) -> dict[str, object]:
    """Translate an internal Message into an OpenAI chat message.

    System messages need no special handling: the OpenAI chat format carries
    system instructions as a `system`-role entry in the `messages` array, so a
    `role="system"` message passes through the generic path unchanged. (The
    Anthropic client must instead lift them to a top-level `system` param.)
    """
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content or "",
        }
    wire: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return wire


def _to_wire_tool(tool: Tool) -> dict[str, object]:
    """Wrap an internal Tool in the OpenAI function-tool envelope."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _from_wire_response(payload: dict[str, Any]) -> Response:
    """Extract a Response from an OpenAI chat-completions payload."""
    choice = payload["choices"][0]
    message = choice["message"]
    text = message.get("content") or None
    if choice.get("finish_reason") == "length":
        # Cut off at the token limit. Tool calls in a truncated reply may
        # have been cut mid-JSON; don't parse them, carry only the text and
        # let the loop stop.
        return Response(text=text, truncated=True)
    # Content and tool_calls may both be present ("let me read the file"
    # followed by the call); both are kept.
    return Response(
        text=text,
        tool_calls=[
            _from_wire_tool_call(call)
            for call in message.get("tool_calls") or []
        ],
    )


def _from_wire_tool_call(call: dict[str, Any]) -> ToolCall:
    """Parse one OpenAI-format tool call. Its arguments arrive as a JSON string.

    Open-weights models sometimes emit arguments that are not valid JSON, or
    are valid JSON but not an object. Neither can become a `ToolCall`, so
    raise `ModelResponseError` rather than let `json.loads` escape as a
    traceback. The raw text is included so a reader can see what the model
    actually produced.
    """
    name = call["function"]["name"]
    raw = call["function"]["arguments"] or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelResponseError(
            f"tool call {name!r} has malformed JSON arguments ({exc}): {raw!r}"
        ) from exc
    if not isinstance(arguments, dict):
        raise ModelResponseError(
            f"tool call {name!r} arguments must be a JSON object: {raw!r}"
        )
    return ToolCall(id=call["id"], name=name, arguments=arguments)
