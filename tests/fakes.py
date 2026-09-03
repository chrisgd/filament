"""Offline test doubles shared across the test modules.

None of these touch a network or a terminal. `FakeClient` stands in for a
`ModelClient` by replaying scripted `Response` objects; every loop,
interactive, and CLI test builds on it.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from filament.tools.base import Registry, Tool
from filament.types import Message, Response


class FakeClient:
    """A ModelClient that replays a scripted list of Responses.

    Records the message list of every call so tests can assert what the
    model would have seen.
    """

    def __init__(self, scripted: list[Response]) -> None:
        self._scripted = list(scripted)
        self.calls: list[list[Message]] = []

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        self.calls.append(list(messages))
        return self._scripted.pop(0)


class FailThenSucceed:
    """A ModelClient that raises `error` on its first call, then finishes."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        self.call_count += 1
        if self.call_count == 1:
            raise self._error
        return Response(text="recovered")


def registry_with(
    name: str, handler: Callable[[dict[str, object]], str]
) -> Registry:
    """A registry holding one tool with an empty schema and `handler`."""
    registry = Registry()
    registry.register(
        Tool(
            name=name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    return registry


def status_error(status: int, body: str) -> httpx.HTTPStatusError:
    """An HTTPStatusError as `raise_for_status()` would build it, offline."""
    request = httpx.Request("POST", "https://backend.example/v1/messages")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status}'", request=request, response=response
    )
