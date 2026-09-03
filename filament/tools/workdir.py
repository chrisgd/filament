"""The working-directory boundary shared by the file and shell tools.

Filament's tools act on the real filesystem with the user's own permissions.
Without a boundary, one hallucinated path can read `~/.ssh` into the
transcript or write over `~/.bashrc`. This module holds a single root
directory (the directory Filament was launched from, or `FILAMENT_WORKDIR`)
and resolves tool paths against it, refusing any that land outside.

This is a path check, not a sandbox: `run_shell` runs with the root as its
working directory, but a shell command can still name any path it likes.
Real confinement needs an OS-level mechanism and is out of scope. See
@specs/SPEC-workdir-boundary.md.

Module-level state, for the same reason `ask_user` holds its streams that
way: the `Tool` handler signature is fixed at `(dict) -> str`.
"""

from __future__ import annotations

from pathlib import Path

_root: Path | None = None


def set_root(path: str | Path) -> None:
    """Set the boundary to an existing directory. Symlinks are resolved."""
    global _root
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_dir():
        raise ValueError(f"working directory does not exist: {path}")
    _root = candidate


def root() -> Path:
    """The current boundary: the process cwd until `set_root` is called."""
    return _root if _root is not None else Path.cwd().resolve()


def resolve_within(path: str, tool: str) -> Path:
    """Resolve `path` against the root and require it to stay inside.

    Relative paths are taken from the root, not from the process cwd. `~` is
    expanded and symlinks are followed before the check, so neither can be
    used to reach outside. Raises `PermissionError` (an `OSError`) so the
    loop feeds it back to the model as text like any other tool failure.
    """
    base = root()
    candidate = (base / Path(path).expanduser()).resolve()
    if not candidate.is_relative_to(base):
        raise PermissionError(
            f"{tool}: path escapes the working directory ({base}): {path}"
        )
    return candidate
