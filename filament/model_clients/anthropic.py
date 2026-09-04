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
# Thinking tokens count against max_tokens. 4096 was enough for text and
# tool calls alone; with thinking on, which Claude 5 models do by default,
# it is not. A reply that still hits the cap surfaces as Response.truncated.
_MAX_TOKENS = 16384
_TIMEOUT_SECONDS = 120.0
# The key under which this client stores thinking blocks in provider_state.
# The shape is this client's business alone; see _thinking_blocks.
_THINKING_BLOCKS = "thinking_blocks"


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
    grouped together. Assistant turns are rendered by `_assistant_content`,
    which puts any thinking blocks back where the API expects them.
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
        if message.role == "assistant":
            wire.append(
                {"role": "assistant", "content": _assistant_content(message)}
            )
        else:
            wire.append(
                {"role": message.role, "content": message.content or ""}
            )
    return wire


def _assistant_content(message: Message) -> str | list[dict[str, object]]:
    """Render an assistant turn's content for replay.

    Thinking blocks come first, then the text, then the `tool_use` blocks:
    the order the API returned them, which it expects back. A plain text
    turn with nothing to replay stays a bare string, the shape this client
    has always sent.
    """
    thinking = _thinking_blocks(message)
    if not thinking and not message.tool_calls:
        return message.content or ""
    content: list[dict[str, object]] = list(thinking)
    if message.content:
        content.append({"type": "text", "text": message.content})
    for call in message.tool_calls or []:
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    return content


def _thinking_blocks(message: Message) -> list[dict[str, object]]:
    """The thinking blocks this client stored on `message`, verbatim.

    `provider_state` is opaque to everyone but the client that produced it.
    This client stores `{"thinking_blocks": [...]}`; anything else is not
    ours and yields nothing.
    """
    state = message.provider_state
    if isinstance(state, dict):
        blocks = state.get(_THINKING_BLOCKS)
        if isinstance(blocks, list):
            return list(blocks)
    return []


def _to_wire_tool(tool: Tool) -> dict[str, object]:
    """Translate an internal Tool into an Anthropic tool definition."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _from_wire_response(payload: dict[str, Any]) -> Response:
    """Extract a Response from the content blocks of a Messages-API reply.

    `thinking` and `redacted_thinking` blocks are kept verbatim, in order,
    as `provider_state`. They are signed: the API rejects a continuation
    that drops or edits them, so they go back byte-for-byte on replay and
    nothing outside this client reads them. A `thinking` block whose text
    is empty is normal on current models, where the reasoning is omitted by
    default; the block still carries its signature and is replayed the same.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    thinking_blocks: list[dict[str, object]] = []
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
        elif block["type"] in ("thinking", "redacted_thinking"):
            thinking_blocks.append(block)
    text = "".join(text_parts) or None
    provider_state = (
        {_THINKING_BLOCKS: thinking_blocks} if thinking_blocks else None
    )
    if payload.get("stop_reason") == "max_tokens":
        # Cut off at the token limit. Tool calls in a truncated reply cannot
        # be trusted to be complete; carry only the text and let the loop
        # stop.
        return Response(text=text, truncated=True, provider_state=provider_state)
    # Text and tool_use blocks may both be present ("let me read the file"
    # followed by the call); both are kept.
    return Response(
        text=text, tool_calls=tool_calls, provider_state=provider_state
    )
