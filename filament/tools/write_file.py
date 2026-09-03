"""The `write_file` tool: write text to a file, creating parent directories.

Paths are confined to the working directory; see `workdir.py`. That is the
only guard. Inside the boundary the tool overwrites whatever it is pointed
at, so the boundary is what makes it safe to hand to a student.
"""

from __future__ import annotations

from .base import Tool
from .workdir import resolve_within


def _write_file(arguments: dict[str, object]) -> str:
    path = arguments["path"]
    content = arguments["content"]
    if not isinstance(path, str):
        raise ValueError("write_file: 'path' must be a string")
    if not isinstance(content, str):
        raise ValueError("write_file: 'content' must be a string")
    target = resolve_within(path, "write_file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {path}"


write_file_tool = Tool(
    name="write_file",
    description=(
        "Write text content to a file in the working directory, creating "
        "parent directories as needed. Overwrites the file if it already "
        "exists. Paths outside the working directory are refused."
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
