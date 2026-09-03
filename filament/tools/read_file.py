"""The `read_file` tool: read a file's contents from disk.

Paths are confined to the working directory; see `workdir.py`.
"""

from __future__ import annotations

from .base import Tool
from .workdir import resolve_within


def _read_file(arguments: dict[str, object]) -> str:
    path = arguments["path"]
    if not isinstance(path, str):
        raise ValueError("read_file: 'path' must be a string")
    target = resolve_within(path, "read_file")
    try:
        with open(target, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"read_file: no such file: {path}") from None
    except OSError as exc:
        raise OSError(f"read_file: could not read {path}: {exc}") from None


read_file_tool = Tool(
    name="read_file",
    description=(
        "Read a file from the working directory and return its contents as "
        "text. Relative paths resolve from the working directory; paths "
        "outside it are refused."
    ),
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
