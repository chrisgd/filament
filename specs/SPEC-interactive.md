# Filament — Interactive Mode

A design spec for Filament's interactive (multi-turn) mode. This is a component spec: the original whole-project build spec is `SPEC.md` at the repo root, and the load-bearing constraints in `CLAUDE.md` apply here too.

## Purpose

Filament's original CLI is strictly one-shot: `filament "task"` runs the agent loop to completion and exits. That's the right default for scripting and for the teaching point about agent design — but for exploration, faculty and students want to ask follow-up questions ("now refactor that file") that depend on what the agent already saw. Interactive mode adds that: a multi-turn conversation in which assistant turns, tool calls, and tool results accumulate across user prompts.

The one-shot CLI path stays untouched. Interactive mode is an additional entry point, not a replacement.

## Scope

In v1:

- `filament` with no arguments launches interactive mode.
- A `Conversation` abstraction owns the conversation state.
- Slash commands: `/exit`, `/reset`, `/messages`, `/help`.
- A single session transcript covers the whole interactive run.
- The existing `httpx`-error handling from issues 9–10 extends to interactive mode — a transport blip inside a turn does not kill the session.

Explicitly deferred:

- Compaction / summarization of long conversations.
- Arrow-key history, readline editing, prompt_toolkit (no new dependencies).
- Mid-turn Ctrl-C cancellation (the model call is a blocking `httpx.post`).
- A `-i` / `--interactive` flag (no-args is sufficient).
- A hybrid `filament -i "starting task"` mode.

## User-facing behavior

### Invocation

```
filament                       # interactive mode
filament "task"                # one-shot, unchanged
echo "task" | filament         # interactive mode, runs one turn, exits on EOF
```

### Session shape

```
$ filament
filament interactive mode. /help for commands, /exit to quit.

> read the README
  …agent runs to completion, prints final text…
> now summarize what you found
  …agent remembers the README from the previous turn…
> /messages
  18 messages (1 system, 4 user, 7 assistant, 6 tool_result)
> /reset
  conversation reset. 1 message (system).
> /exit
[transcript: filament-sessions/20260523T010503000000.jsonl]
```

The prompt is a plain `> `. No readline, no styling.

### Slash commands

Commands are recognized only when the line **exact-matches** one of the known commands. A line that starts with `/` but doesn't match (e.g., `/etc/hosts has a typo`) is treated as a regular task and sent to the agent. This avoids surprise for paths and other natural `/`-prefixed input.

- `/exit` — close the session and exit cleanly. Return code 0.
- `/reset` — drop the conversation back to just the system message. Logs a `conversation_reset` event into the same session transcript; the transcript stays continuous and honest about what the model saw before vs. after the reset.
- `/messages` — print a breakdown of accumulated messages by role: `N messages (X system, Y user, Z assistant, W tool_result)`.
- `/help` — print the list of commands and the exit-line hint.

### Empty input and exits

- Empty line → re-prompt. No model call.
- EOF on stdin (Ctrl-D, piped input exhausted) → clean exit, return 0.
- `KeyboardInterrupt` between turns → print `interrupted.` and continue. The session and conversation are preserved.
- `KeyboardInterrupt` mid-turn is not handled cleanly — the model call is a blocking `httpx.post`. This is a documented limitation, not an attempt to paper over it.

### Errors inside a turn

A `httpx.HTTPError` raised by the model client during a turn is caught at the read-loop level, printed as `error: <Type>: <message>` to stderr, and the loop continues. The session, conversation state, and transcript file remain intact. This matches the one-shot path's error format (issue 9).

Any other exception escapes the loop. Genuinely unexpected exceptions are bugs, and a traceback is the right surfaced behavior in teaching infra.

## Internal design

### `Conversation` (in `filament/agent.py`)

```python
class Conversation:
    def __init__(
        self,
        client: ModelClient,
        registry: Registry,
        session: Session,
        backend: str,
    ) -> None: ...

    messages: list[Message]              # always starts with the system message

    def send(self, task: str) -> str:
        """Append the user task, run the read/decide/act/observe loop
        until a final text response or MAX_ITERATIONS, return the text."""

    def reset(self) -> None:
        """Drop messages back to just the system message; logs a
        conversation_reset event into the session transcript."""
```

Invariants:

- `messages` always starts with a single `Message(role="system", content=SYSTEM_PROMPT)`.
- `MAX_ITERATIONS = 25` is a **per-`send()`** cap, not a per-conversation cap. One user task gets one bounded reasoning budget.
- The read/decide/act/observe loop body itself is unchanged from the original `run_agent`. `Conversation.send()` is where it now lives.

`run_agent` becomes a thin wrapper:

```python
def run_agent(task, client, registry, session, backend) -> str:
    return Conversation(client, registry, session, backend).send(task)
```

This preserves `run_agent`'s signature and return type. Every existing call site keeps working.

### `run_interactive` (in `filament/interactive.py`)

```python
def run_interactive(
    client: ModelClient,
    registry: Registry,
    session: Session,
    backend: str,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
```

Constructs one `Conversation`. Uses the single passed-in `Session` for the whole run (one continuous JSONL transcript). Reads lines from `stdin`, prints results and prompts to `stdout`. The injectable `stdin`/`stdout` exist so tests can drive the loop with `io.StringIO` and so faculty can pipe input.

### `Session.log_reset` (additive in `filament/session.py`)

```python
def log_reset(self) -> None:
    """Record a /reset event: { "event": "conversation_reset", "timestamp": ... }"""
```

One new event type. The existing four `log_*` methods and the context-manager protocol are unchanged.

### CLI dispatch (in `filament/cli.py`)

The `task` positional becomes optional (`nargs="?"`). After `build_client` / `build_registry` / `new_session`, `cli.main()` dispatches:

- `args.task is None` → `run_interactive(...)`
- otherwise → `run_agent(...)`

The same `try / except httpx.HTTPError / finally session.close()` wraps both paths, so the issue-9 clean-error behavior covers interactive mode too.

`cli.py` stays a dispatcher. It does not own a REPL or slash-command parsing — those live in `interactive.py`.

## Constraints honored

- The agent loop body and its `MAX_ITERATIONS = 25` cap are preserved verbatim.
- The `Tool` contract and the `ModelClient` Protocol are unchanged.
- No per-backend logic in interactive code.
- All new tests run offline (stdin/stdout stubbed via `io.StringIO`, model client stubbed via the existing `FakeClient` pattern).
- No new dependencies. `httpx` and `pytest` remain it.
- `cli.py` stays a thin dispatcher.

## Considered and rejected

- **Threading an optional `messages` list through `run_agent`.** Cheaper than introducing a class, but it would (a) require callers to know the system prompt isn't theirs to construct, leaking that detail outward, and (b) make the "fresh task" and "continued conversation" cases look identical at the call site despite being semantically different. The `Conversation` class makes state ownership obvious, which is worth one extra abstraction in a teaching codebase.
- **Stateless REPL** (each line a fresh `run_agent` call). The agent forgets everything between prompts — barely better than re-running `filament "..."` in the shell. Defeats the purpose.
- **Auto-warning on message growth.** Considered as an additional safety net beyond `/messages`. Rejected for v1 to keep behavior simple and the surface small; `/messages` gives the user the same information on demand.
- **`readline` / `prompt_toolkit` for arrow-key history.** Adds a dependency CLAUDE.md doesn't sanction. Faculty can live without history in v1.
- **A `-i` / `--interactive` flag.** Redundant when `nargs="?"` is enough to detect "no task given." Adds a code path for no gain.

## Testing strategy

All tests offline. The existing `FakeClient` in `tests/test_agent.py` (a `ModelClient` replaying scripted `Response` objects) is reused for `Conversation` and interactive tests.

- `tests/test_agent.py` — multi-turn accumulation: two `.send()` calls in sequence; the second model_call sees history from the first. `Conversation.reset()` empties accumulated turns; the next `.send()` starts fresh.
- `tests/test_session.py` — `log_reset()` writes the expected event type.
- `tests/test_interactive.py` (new) — drives `run_interactive` with `io.StringIO` for stdin/stdout and a stubbed conversation or model client. Covers: two-turn happy path + `/exit`; `/reset` clears state; `/messages` prints the expected breakdown; unknown `/foo` treated as a task; EOF exits cleanly; empty line re-prompts (no model call); `httpx` error inside a turn doesn't kill the loop.
- `tests/test_cli.py` — parallel happy/error tests for the interactive branch. `cli.run_interactive` is stubbed the same way `cli.run_agent` is today.

## Future work

When and if the need is real, not before:

- `Conversation.compact()` or `.summarize_older(n)` for long-running conversations approaching context limits.
- Mid-turn cancellation (requires a non-blocking model client).
- Arrow-key history via `readline` (a real dependency decision).
- Hybrid `filament -i "starting task"` invocation.
