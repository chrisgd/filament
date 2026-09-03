# Filament — Activity Signals

A design spec for adding minimal, transitional progress output to interactive mode. This is a component spec; load-bearing constraints in `CLAUDE.md` apply.

## Purpose

Interactive mode (per `@specs/SPEC-interactive.md`) runs silent during a turn. A user types a task; the read/decide/act/observe loop iterates, calling the model and dispatching tools; only the model's final text is printed at the end. The blocking `httpx.post` to the backend — especially against Rosie, where latency varies with checkpoint and load — produces a perceived freeze that's indistinguishable from a hung CLI.

Earlier design discussion considered token streaming as a fix. Streaming smears the model's answer across the terminal as it forms — a strong UX signal, but one that obscures the very loop the harness exists to teach. CLAUDE.md explicitly defers it: *"Streaming, retries, fancy formatting, telemetry — all of these are valid future work but not yet."*

This spec covers a different lever: dead-reckoning *activity* signals. Rather than showing the model's content as it generates, surface the loop's *transitions* — model call started, tool dispatched, tool returned. The information is already in the JSONL session transcript; mirroring it to stdout makes the loop visible while it turns. This is a clarity-positive change: a student watching `[tool] read_file path="README.md"` flash by on each iteration is *learning the loop*, not skimming prose.

## Scope

In v1:

- A `TurnReporter` Protocol with four hook points: `model_call_start`, `model_call_end`, `tool_call`, `tool_result`.
- A `ConsoleReporter` implementation writing one ASCII line per event to a stream.
- `Conversation.__init__` accepts an optional `reporter`; when `None`, behavior is byte-identical to today.
- `run_interactive` constructs a `ConsoleReporter` and threads it through. `run_agent` (one-shot) does not.

Explicitly deferred:

- Background-thread spinner / continuous ticking while the model call blocks.
- Per-event verbosity flags (`--quiet`, `--verbose`).
- One-shot mode activity output.
- Colorization or TTY-aware styling.
- Per-tool customization of the result summary.

## User-facing behavior

A typical interactive turn:

```
$ filament
filament interactive mode. /help for commands, /exit to quit.

> read the README and summarize it
[thinking...]
Let me read the README first.
[tool] read_file path="README.md"
[tool ok] read_file (412 bytes)
[thinking...]
The README describes Filament's architecture: a four-layer harness with...
> /exit
[transcript: filament-sessions/20260523T180102000000.jsonl]
```

### Output rules

- **ASCII only.** No Unicode arrows, no emoji, no ANSI color codes. Portable across terminals and student platforms.
- **One line per event.** The line is complete before the next event fires; no in-place updates.
- `[thinking...]` prints just before each model call. It is not erased — the next event (either a `[tool]` line or the model's final text) is the visible signal that the call completed.
- `[tool] <name> <key>="<value>" ...` prints when the loop dispatches a tool. Arguments are rendered as `key="value"` pairs in dict-iteration order; values longer than ~60 characters are truncated with a trailing `...`.
- `[tool ok] <name> (<N> bytes)` prints when a tool succeeds. `N` is `len(result)` of the returned string.
- `[tool err] <name>: <ErrorType>` prints when a tool raised — the loop's `_dispatch` already turns the exception into `error: <ErrorType>: <message>`, and the reporter surfaces just the type to keep the line compact. The full message remains in the transcript and in the model's next message context.
- `model_call_end` receives the `Response`. `ConsoleReporter` prints the model's text bare when the turn also carries tool calls (`Let me read the README first.`): bracketed lines are the harness, bare lines are the model. A final answer is not printed here; the read-loop prints it as the turn's result, so it appears once.

### Edge cases

- **Empty tool result** → `[tool ok] <name> (0 bytes)`.
- **Multi-line tool result** → line count isn't surfaced; byte count only. The full multi-line content reaches the model's next message context and the transcript as usual.
- **Very long argument value** → truncated in the echoed line; full value preserved in the transcript.
- **Reporter raising.** Shouldn't happen for `ConsoleReporter` (it just writes to a stream), but if it did, the exception propagates out of `send()`. The reporter is inside the loop's trust boundary, not a tool — there's no error-recovery contract for it.

## Internal design

### `TurnReporter` Protocol (in `filament/agent.py`)

```python
from typing import Protocol

class TurnReporter(Protocol):
    def model_call_start(self, iteration: int) -> None: ...
    def model_call_end(self, response: Response) -> None: ...
    def tool_call(self, name: str, arguments: dict[str, object]) -> None: ...
    def tool_result(self, name: str, result: str) -> None: ...
```

Lives next to the `ModelClient` Protocol — both are small contract surfaces the loop depends on. No default implementation in `agent.py`; `Conversation` guards every call with `if self._reporter:`.

### `Conversation` change (in `filament/agent.py`)

```python
class Conversation:
    def __init__(
        self,
        client: ModelClient,
        registry: Registry,
        session: Session,
        backend: str,
        reporter: TurnReporter | None = None,
    ) -> None: ...
```

In `send()`, the reporter fires at the same four points where `session.log_*` already fires:

- Just before `self._client.complete(...)` → `reporter.model_call_start(iteration)`.
- Just after the model returns, before processing the response → `reporter.model_call_end(response)`.
- Just before each `_dispatch(registry, name, arguments)` → `reporter.tool_call(name, arguments)`.
- Just after `_dispatch` returns → `reporter.tool_result(name, result)`. The `result` string is whatever `_dispatch` returned, including the `error: ...` form for tool exceptions.

`run_agent` is unchanged — it still calls `Conversation(...)` without a `reporter`, so one-shot mode stays silent.

### `ConsoleReporter` (in `filament/interactive.py`)

```python
class ConsoleReporter:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def model_call_start(self, iteration: int) -> None:
        print("[thinking...]", file=self._stream, flush=True)

    def model_call_end(self, response: Response) -> None:
        if response.tool_calls and response.text:
            print(response.text, file=self._stream, flush=True)

    def tool_call(self, name: str, arguments: dict[str, object]) -> None:
        rendered = _format_args(arguments)
        line = f"[tool] {name} {rendered}".rstrip()
        print(line, file=self._stream, flush=True)

    def tool_result(self, name: str, result: str) -> None:
        if result.startswith("error: "):
            err_type = result.split(":", 2)[1].strip()
            print(f"[tool err] {name}: {err_type}", file=self._stream, flush=True)
        else:
            print(f"[tool ok] {name} ({len(result)} bytes)", file=self._stream, flush=True)
```

`_format_args` is a module-private helper rendering the dict as space-separated `key="value"` pairs with ~60-char truncation. Lives in `interactive.py` alongside `ConsoleReporter`.

### Wiring (in `filament/interactive.py`)

At the top of `run_interactive`, after `configure_streams(stdin, stdout)` (already present for `ask_user`), construct the reporter and thread it in:

```python
reporter = ConsoleReporter(stdout)
conversation = Conversation(client, registry, session, backend, reporter=reporter)
```

No change to `cli.py`. No change to `run_agent`.

## Constraints honored

- `ModelClient` Protocol unchanged.
- `Tool` contract unchanged.
- `Registry.invoke` unchanged (no pre/post hooks added).
- Agent loop body unchanged in shape — four optional reporter calls slot in at the same four control-flow points as the existing `session.log_*` calls.
- The loop owns control flow; the reporter owns presentation. No tool-specific branching anywhere in the reporter.
- No new dependencies.
- One-shot CLI behavior preserved exactly: no reporter, no progress output.
- All new tests run offline via the existing `FakeClient` pattern.
- No per-backend logic; the reporter sits above the model-client layer.

## Considered and rejected

- **Token streaming.** Strongest UX signal but obscures the loop. CLAUDE.md explicitly defers it. Activity signals deliver the perceptual win — "the harness is alive, and here's what step it's on" — without showing the model's working text, which is the part that hides the abstraction.
- **Background-thread spinner.** Closer to literal dead reckoning (continuous motion). Rejected for v1: threading complicates tests (the `FakeClient` returns instantly, so a spinner never fires meaningfully), risks line-tearing if a tool prints concurrently, and the perceived freeze is one discrete event — the model call — not a continuum. Printing `[thinking...]` immediately before the blocking call provides "I started, I'm still going" without the complexity. Revisit only if real Rosie latency shows the gaps still feel dead.
- **Tapping `Session` writes via subclassing or a sink list.** Tempting because the session already receives every event. Rejected: conflates structured logging (JSONL, for grep/parse) with human-readable progress (a different format and a different audience). Two sinks called from one set of loop call sites is cleaner than one polymorphic sink.
- **Single kwarg-bag method**, `report(event: str, **fields)`. Smaller surface, but worse typing, worse discoverability, and event-name typos become runtime bugs. Four named methods cost almost nothing.
- **Pre/post hooks on `Registry.invoke`.** Would let the registry emit events directly. Rejected: couples the registry to presentation. The loop is the right call site — it already sequences model calls and tool calls and owns iteration order.
- **`--verbose` flag for one-shot mode.** Useful eventually but not now. One-shot is for scripts and grep and stays clean; interactive is verbose by default. Adding a flag now anticipates a need that may not arrive.
- **Per-tool customization of the result summary** (first line for `read_file`, exit code for `run_shell`). Real value, but introduces tool-specific presentation logic into the reporter. Defer until a concrete use case justifies the surface.

## Testing strategy

All tests offline. Reuse the existing `FakeClient` pattern from `tests/test_agent.py`.

- **`tests/test_agent.py` — reporter dispatch.** Define a `RecordingReporter` test double that appends each call (with args) to a list. Construct a `Conversation` with a `FakeClient` scripted for: tool call → tool result → final text. Assert the recorded sequence is exactly `[model_call_start(1), model_call_end(response), tool_call(name, args), tool_result(name, result), model_call_start(2), model_call_end(response)]`. Separate test: omitting `reporter` produces no calls (verified by the fact that the test fake is never constructed).
- **`tests/test_interactive.py` — `ConsoleReporter` output.** Drive `run_interactive` with `io.StringIO` for stdin and stdout, scripted `FakeClient`. Assert the captured stdout contains the expected `[thinking...]`, `[tool] read_file path="README.md"`, `[tool ok] read_file (N bytes)`, and final-text lines in order. Edge cases: a tool that returns `"error: FileNotFoundError: ..."` produces `[tool err] read_file: FileNotFoundError`; an argument value longer than 60 chars is truncated in the echoed line.
- **One-shot regression.** Existing one-shot tests already exercise `run_agent` and don't construct a reporter; add an explicit assertion that the captured stdout from a one-shot invocation contains no `[thinking...]` or `[tool]` substrings. Confirms silence is preserved.

A separate `tests/test_reporter.py` is acceptable if the suite grows; v1 keeps the reporter tests in the modules they touch.

## Future work

When and if real needs arise, not before:

- Background-thread `[thinking...]` ticker if Rosie latency shows the gap between events feels dead in practice.
- One-shot `--verbose` flag reusing `ConsoleReporter`.
- Per-tool result summaries (first line for `read_file`, exit code for `run_shell`).
- Colorization or TTY-aware styling (a real dependency decision, or a no-deps `\033[...]` approach gated on `isatty()`).
- A truncation policy for very large tool results in the echoed line (currently shown as `(N bytes)` only; could add a first-N-chars preview).

## Amendments

- 2026-09-02: `model_call_end` receives the `Response`, and `ConsoleReporter` prints the model's interstitial text. Issue 16 made that text available; before it, the hook had nothing to show.
