"""The Tool contract and the Registry.

`Tool` and `Registry` are load-bearing abstractions. Do not change their shape
to accommodate a new tool or a new backend — see CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    """A declarative tool definition.

    Attributes:
        name: The identifier the model calls the tool by. Unique within a
            `Registry`.
        description: What the tool does, shown to the model. Any side
            effects (files written, commands run) must be stated here.
        parameters: A JSON Schema dict describing the handler's arguments.
            Sent to the backend as the tool's input schema.
        handler: The function that does the work. Takes the parsed
            arguments as a dict and returns a string result. Raises on
            failure; the loop feeds the error back to the model as text.
    """

    name: str
    description: str
    parameters: dict[str, object]
    handler: Callable[[dict[str, object]], str]


class Registry:
    """Collects tools and is the only path by which they are invoked."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Add a tool. Raises if a tool with the same name already exists."""
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def tools(self) -> list[Tool]:
        """Return all registered tools as the agent loop / clients see them."""
        return list(self._tools.values())

    def invoke(self, name: str, arguments: dict[str, object]) -> str:
        """Dispatch a tool call by name. Raises KeyError for unknown tools."""
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name].handler(arguments)
