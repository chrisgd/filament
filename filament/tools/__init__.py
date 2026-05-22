"""The tool package: registry plus the registered tools.

To add a tool: create a module here exporting one `Tool` instance, then import
it and register it in `build_registry` below. Nothing else changes.
"""

from __future__ import annotations

from .base import Registry, Tool
from .read_file import read_file_tool
from .run_shell import run_shell_tool
from .write_file import write_file_tool

__all__ = ["Registry", "Tool", "build_registry"]


def build_registry() -> Registry:
    """Construct a registry with the standard set of tools registered."""
    registry = Registry()
    registry.register(read_file_tool)
    registry.register(write_file_tool)
    registry.register(run_shell_tool)
    return registry
