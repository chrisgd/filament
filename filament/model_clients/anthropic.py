"""Anthropic model client: the Messages API over httpx.

This client speaks the Anthropic Messages wire format directly, visibly, on
purpose, to make it clear how the API works and to avoid unnecessary translation layers.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..tools.base import Tool
from ..types import Message, Response, ToolCall

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 4096
_TIMEOUT_SECONDS = 120.0


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError(
                "AnthropicClient requires FILAMENT_ANTHROPIC_API_KEY"
            )
        self._api_key = api_key
        self._model = model

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "cache_control": {"type": "ephemeral"},
            "messages": _to_wire_messages(messages),
        }
        system = _system_prompt(messages)
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [_to_wire_tool(t) for t in tools]
        reply = httpx.post(
            _API_URL,
            json=body,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        reply.raise_for_status()
        return _from_wire_response(reply.json())


def _system_prompt(messages: list[Message]) -> str:
    """Join system-role messages for the Messages-API top-level `system` param.

    The Messages API has no `system` role inside `messages`; system
    instructions go in a dedicated top-level field. Lifting them here is
    exactly the wire-format difference this client exists to absorb.
    """
    parts = [m.content for m in messages if m.role == "system" and m.content]
    return "\n\n".join(parts)


def _to_wire_messages(messages: list[Message]) -> list[dict[str, object]]:
    """Translate internal Messages into Anthropic Messages-API messages.

    System messages are excluded — they are lifted into the top-level `system`
    request parameter by `_system_prompt`. Tool results are user-role messages
    carrying `tool_result` blocks; consecutive tool results are coalesced into
    one user message, as the API expects results for one assistant turn
    grouped together.
    """
    wire: list[dict[str, object]] = []
    pending_results: list[dict[str, object]] | None = None
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            block: dict[str, object] = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content or "",
            }
            if pending_results is None:
                pending_results = [block]
                wire.append({"role": "user", "content": pending_results})
            else:
                pending_results.append(block)
            continue

        pending_results = None
        if message.role == "assistant" and message.tool_calls:
            content: list[dict[str, object]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            wire.append({"role": "assistant", "content": content})
        else:
            wire.append(
                {"role": message.role, "content": message.content or ""}
            )
    return wire


def _to_wire_tool(tool: Tool) -> dict[str, object]:
    """Translate an internal Tool into an Anthropic tool definition."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _from_wire_response(payload: dict[str, Any]) -> Response:
    """Extract a Response from the content blocks of a Messages-API reply."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in payload["content"]:
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block["input"],
                )
            )
    text = "".join(text_parts) or None
    if payload.get("stop_reason") == "max_tokens":
        # Cut off at the token limit. Tool calls in a truncated reply cannot
        # be trusted to be complete; carry only the text and let the loop
        # stop.
        return Response(text=text, truncated=True)
    # Text and tool_use blocks may both be present ("let me read the file"
    # followed by the call); both are kept.
    return Response(text=text, tool_calls=tool_calls)
