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

    `parameters` is a JSON Schema dict describing the handler's arguments.
    `handler` takes a dict of those arguments and returns a string result.
    Any side effects must be documented in `description`.
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
