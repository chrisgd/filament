"""The `read_file` tool: read a file's contents from disk."""

from __future__ import annotations

from .base import Tool


def _read_file(arguments: dict[str, object]) -> str:
    path = arguments["path"]
    if not isinstance(path, str):
        raise ValueError("read_file: 'path' must be a string")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"read_file: no such file: {path}") from None
    except OSError as exc:
        raise OSError(f"read_file: could not read {path}: {exc}") from None


read_file_tool = Tool(
    name="read_file",
    description="Read a file from disk and return its contents as text.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read.",
            },
        },
        "required": ["path"],
    },
    handler=_read_file,
)
