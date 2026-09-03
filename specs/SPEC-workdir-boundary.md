# Filament — Working-Directory Boundary

A design spec for confining the file and shell tools to one directory. This is a component spec; load-bearing constraints in `CLAUDE.md` apply.

## Purpose

`read_file`, `write_file`, and `run_shell` act on the real filesystem with the user's own permissions. Nothing stops a hallucinated or adversarial path from reading `~/.ssh/id_rsa` into the transcript or writing over `~/.bashrc`. For faculty demos that was tolerable. For students running Filament on their own laptops or on Rosie login nodes it is not.

This spec adds one root directory that the tools stay inside. It is deliberately the smallest change that makes the tools safe to hand to a student: a path check, not a sandbox. The gap between the two is itself a teaching point (see *What this is not*).

## Scope

In v1:

- One root: the directory Filament is launched from, or `FILAMENT_WORKDIR` if set.
- `read_file` and `write_file` resolve paths against the root and refuse any that land outside it, symlinks included.
- `run_shell` runs with the root as its working directory.
- A shared helper module, `filament/tools/workdir.py`, owns the root and the check.

Explicitly deferred:

- A `--workdir` CLI flag. `cd` and the environment variable cover it.
- Multiple allowed roots, or an allow/deny list.
- Any confinement of what a shell command can do.
- A read-only mode.

## User-facing behavior

```
$ cd ~/proj && filament "add a docstring to util.py"   # root is ~/proj
$ FILAMENT_WORKDIR=~/proj filament "..."                # root is ~/proj, run from anywhere
```

When the model asks for a path outside the root, the tool raises and the loop feeds the error back as text, exactly as it does for a missing file:

```
[tool] write_file path="/home/student/.bashrc" content="..."
[tool err] write_file: PermissionError
```

The model sees `error: PermissionError: write_file: path escapes the working directory (/home/student/proj): /home/student/.bashrc` and can correct course.

A `FILAMENT_WORKDIR` that does not exist is a configuration error, reported the same way as a missing API key: `configuration error: working directory does not exist: ...`, exit code 2.

### Rules

- Relative paths resolve from the root, not from the process cwd. The two coincide by default and differ only when `FILAMENT_WORKDIR` is set.
- Symlinks are resolved before the check. A link inside the root that points outside it is refused.
- `~` is expanded before the check, so `~/.ssh/config` is refused rather than silently created as a literal `~` directory under the root.
- The root itself is inside the boundary. `write_file` may still create parent directories, inside the root only.

### What this is not

`run_shell` is given `cwd=root`. That decides only where relative paths in the command land. A command can still `cat /etc/passwd` or `rm -rf ~`. A path check cannot confine a shell; that takes an operating-system mechanism (a container, a jail, or the permission prompts production harnesses use). The tool's description tells the model to stay inside the working directory, and this spec tells the reader that nothing enforces it. Filament does not pretend otherwise.

## Internal design

### `filament/tools/workdir.py`

```python
def set_root(path: str | Path) -> None:
    """Set the boundary to an existing directory. Raises ValueError otherwise."""

def root() -> Path:
    """The current boundary; the process cwd until set_root is called."""

def resolve_within(path: str, tool: str) -> Path:
    """Resolve `path` against the root; raise PermissionError if it lands outside."""
```

Module-level state, for the same reason `ask_user` holds its streams that way: the `Tool` handler signature is fixed at `(dict) -> str` and CLAUDE.md forbids changing it. `PermissionError` is the raised type because it is an `OSError`, so it flows through `agent._dispatch` as text like every other tool failure, and its name says what happened.

### Tool changes

Each of the three tool modules calls into `workdir` and nothing else about them changes. `read_file` and `write_file` call `resolve_within` before touching the disk and then operate on the resolved path. `run_shell` passes `root()` as `cwd`. Each tool's description states the boundary so the model knows the rule before it breaks it.

### Config and wiring

`config.py` gains `workdir`, read from `FILAMENT_WORKDIR` and defaulting to `os.getcwd()`. `cli.main` calls `workdir.set_root(config.workdir)` next to `build_client`, inside the same `ValueError` handler, so a bad directory is reported as a configuration error.

## Constraints honored

- `Tool` contract, `ModelClient` Protocol, and the agent loop are unchanged.
- No tool-specific logic outside the tool modules. `workdir.py` is shared plumbing like `base.py`, not a tool; it exports no `Tool` instance.
- No new dependencies.
- All new tests run offline against `tmp_path`.

## Considered and rejected

- **Reading the environment variable inside each tool.** Three copies of one default, and it bypasses `config.py`, which CLAUDE.md names as the one place settings load.
- **Passing the root through `registry.invoke`.** Changes the `Tool` handler signature.
- **A per-session `Tool` factory** such as `make_read_file(root)`. More flexible, more machinery, same reasoning that rejected it for `ask_user`.
- **Confining writes but not reads.** One rule is easier to teach than two. And reads are the exfiltration path: everything a tool reads lands in the transcript and in the backend's context.
- **Sandboxing `run_shell` by scanning the command** for absolute paths or `cd`. Trivially bypassed, and a false sense of safety is worse than an honest gap.

## Testing strategy

All offline, in `tests/test_tools.py` unless noted.

- `workdir.resolve_within`: relative path inside the root resolves under it; absolute path inside is accepted; absolute path outside raises `PermissionError`; `..` traversal out of the root raises; a symlink inside the root pointing outside raises; `~` is expanded before the check.
- `workdir.set_root`: a missing directory raises `ValueError`.
- `read_file`: a file outside the root is refused; a relative path resolves from the root even when the process cwd differs.
- `write_file`: a path outside the root is refused and nothing is created; a relative path lands under the root.
- `run_shell`: `pwd` reports the root.
- `tests/test_cli.py`: a nonexistent `FILAMENT_WORKDIR` yields exit code 2 and a `configuration error` line; `main` passes the configured directory to `set_root`.

## Future work

When and if the need is real, not before:

- `--workdir` flag if the environment variable proves awkward in workshops.
- Read-only mode for demonstration settings.
- An opt-in, OS-sandboxed shell tool as a separate module, so the contrast with `run_shell` can be taught side by side.
