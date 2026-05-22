"""The ModelClient Protocol.

Every backend client conforms to this single-method interface. The agent loop
holds a `ModelClient` and never branches on which backend implements it. Do not
change this Protocol to accommodate a new backend — see CLAUDE.md.
"""

from __future__ import annotations

from typing import Protocol

from ..tools.base import Tool
from ..types import Message, Response


class ModelClient(Protocol):
    def complete(
        self, messages: list[Message], tools: list[Tool]
    ) -> Response:
        """Send the conversation and available tools; return the model's reply.

        The implementation translates Filament's internal types to its
        backend's wire format and the backend's reply back to a `Response`.
        Wire-format details must not leak past this method.
        """
        ...
