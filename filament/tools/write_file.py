"""The `write_file` tool: write text to a file, creating parent directories."""

from __future__ import annotations

import os

from .base import Tool


def _write_file(arguments: dict[str, object]) -> str:
    path = arguments["path"]
    content = arguments["content"]
    if not isinstance(path, str):
        raise ValueError("write_file: 'path' must be a string")
    if not isinstance(content, str):
        raise ValueError("write_file: 'content' must be a string")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return f"wrote {len(content)} characters to {path}"


write_file_tool = Tool(
    name="write_file",
    description=(
        "Write text content to a file, creating parent directories as needed. "
        "Overwrites the file if it already exists."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
    handler=_write_file,
)
