"""The ModelClient Protocol.

Every backend client conforms to this single-method interface. The agent loop
holds a `ModelClient` and never branches on which backend implements it. Do not
change this Protocol to accommodate a new backend — see CLAUDE.md.
"""

from __future__ import annotations

from typing import Protocol

from ..tools.base import Tool
from ..types import Message, Response


class ModelResponseError(Exception):
    """The backend replied, but the reply cannot become a `Response`.

    Raised by a model client when translation fails: for example, an
    open-weights model emitting tool-call arguments that are not valid JSON.
    This is distinct from an `httpx` error (the request itself failed) and
    from a tool error (which `agent._dispatch` feeds back to the model as
    text). The CLI and the interactive read-loop catch it and report it as a
    clean `error:` line instead of a traceback.
    """


class ModelClient(Protocol):
    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        """Send the conversation and available tools; return the model's reply.

        The implementation translates Filament's internal types to its
        backend's wire format and the backend's reply back to a `Response`.
        Wire-format details must not leak past this method. Raises
        `ModelResponseError` if the reply cannot be translated.
        """
        ...
