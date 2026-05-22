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
    """Translate an internal Message into an OpenAI chat message."""
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
    message = payload["choices"][0]["message"]
    raw_calls = message.get("tool_calls")
    if raw_calls:
        return Response(
            tool_calls=[
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"] or "{}"),
                )
                for call in raw_calls
            ]
        )
    content = message.get("content")
    if content:
        return Response(final_text=content)
    return Response()
