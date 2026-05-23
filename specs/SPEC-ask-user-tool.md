# Filament — `ask_user` Tool

A design spec for an elicitation tool the agent can invoke when it needs information from the human. This is a component spec; load-bearing constraints in `CLAUDE.md` apply.

## Purpose

The interactive mode shipped in `@specs/SPEC-interactive.md` lets the *human* drive turn-taking: the user types a prompt, the agent runs to completion, the user types another. That's the right shape for exploratory, multi-task sessions.

This spec covers the complementary pattern: the *agent* drives. The agent is given one task, decides for itself when it needs clarification, and calls an `ask_user` tool that prints a question, blocks for a single line of input, and returns the user's response as the tool result. The agent then keeps going.

In agent-design terms this is a *human-in-the-loop tool* (Claude Code's `AskUserQuestion` is the same pattern). For teaching, exposing both designs side by side is the point: students see that turn-taking is itself a design decision the harness can make either way.

## Scope

In v1:

- One tool, `ask_user`, registered alongside the existing `read_file` / `write_file` / `run_shell` tools. One module per CLAUDE.md's procedure.
- Single free-form question; single-line response.
- Works in both one-shot mode (`filament "task"`) and inside interactive mode (the agent can call `ask_user` mid-turn while the user-driven read-loop is in progress).
- Configurable input / output streams so tests drive the handler with `io.StringIO` and so interactive mode can share its read-loop's streams.

Explicitly deferred:

- Structured-choice variant (a `choices: list[str]` parameter or a separate `ask_user_choice` tool). v1 is free-form text only.
- Multi-line input.
- Per-question timeout or cancellation.
- Server-side validation / regex constraints on the response.
- A `SPEC-tools.md` umbrella spec for the tool layer (revisit if more elicitation-shaped tools emerge).

## User-facing behavior

When the agent invokes the tool, the user sees a marked prompt that makes it clear the *model* is asking — not the harness:

```
[ask_user] Should I rename the class to AuthService or AuthClient?
> AuthService please
```

The user's response (`AuthService please` here, with the trailing newline stripped) is returned as the tool result. The agent then continues — typically using the answer to make its next decision.

A complete one-shot session might look like:

```
$ filament "refactor the auth module to use clearer names"
[ask_user] I see two natural rename targets: AuthService or AuthClient. Which do you prefer?
> AuthService
Refactored 4 references across 3 files. Tests pass.

[transcript: filament-sessions/20260523T180102000000.jsonl]
```

In interactive mode the tool call happens mid-turn. The read-loop is waiting for the model to return a final text; the model decides instead to call `ask_user`; the user answers; the model finishes the turn:

```
$ filament
filament interactive mode. /help for commands, /exit to quit.

> refactor the auth module to use clearer names
[ask_user] AuthService or AuthClient?
> AuthService
Refactored 4 references across 3 files. Tests pass.
> now run the test suite
…
```

### Edge cases

- **EOF while waiting for a response** (Ctrl-D, piped input exhausted): the handler raises `EOFError`. `agent._dispatch` catches it and returns `error: EOFError: …` as the tool result. The model sees the error in conversation and decides what to do (usually emits a final text saying it couldn't get a response). No special-casing in the agent loop.
- **Empty response** (user just hits Enter): treated as a real empty-string answer and returned verbatim. The model can interpret as it likes — possibly re-asking.
- **Whitespace-only response**: returned as-is, after stripping only the trailing newline. The model can decide how to interpret.
- **Multiple lines pasted at once**: only the first line is consumed. Subsequent lines are read by whoever owns stdin next (the interactive read-loop, or a subsequent `ask_user` call). Documented limitation.

## Internal design

### Tool definition (in `filament/tools/ask_user.py`)

```python
from filament.tools.base import Tool

ask_user = Tool(
    name="ask_user",
    description=(
        "Ask the user a clarifying question and wait for their single-line "
        "response. Use this when you need information that isn't available "
        "from the other tools — confirmation, a choice between options, "
        "missing context. Returns the user's response as a string."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        "required": ["question"],
    },
    handler=_handler,
)
```

### Handler

```python
def _handler(arguments: dict[str, object]) -> str:
    question = arguments["question"]
    print(f"[ask_user] {question}", file=_output_stream)
    print("> ", end="", file=_output_stream, flush=True)
    line = _input_stream.readline()
    if line == "":
        raise EOFError("ask_user: stdin closed before user responded")
    return line.rstrip("\n")
```

### Stream configuration

The handler reads from a module-level `_input_stream` and writes to `_output_stream`, both defaulting to `sys.stdin` / `sys.stdout`. A `configure_streams(stdin, stdout)` function sets them:

```python
_input_stream: TextIO = sys.stdin
_output_stream: TextIO = sys.stdout

def configure_streams(stdin: TextIO, stdout: TextIO) -> None:
    """Override the streams used by the ask_user handler.

    Called by interactive mode so the tool reads from the same stdin the
    read-loop is using. Tests call this directly with io.StringIO."""
    global _input_stream, _output_stream
    _input_stream = stdin
    _output_stream = stdout
```

Interactive mode calls `configure_streams(stdin, stdout)` at the top of `run_interactive` so a mid-turn `ask_user` call reads from the same injected streams as the read-loop. One-shot CLI mode does not call it; defaults apply.

### Registration

Add the import to `filament/tools/__init__.py` and register the singleton in the same `build_registry` step as the existing tools. Follows CLAUDE.md's *Adding a New Tool* procedure verbatim.

## Constraints honored

- **One tool per module.** `filament/tools/ask_user.py` defines exactly one `Tool` instance.
- **The Tool contract is unchanged.** Handler is still `(arguments: dict) -> str`. The stream configuration is module-level state, not a contract change.
- **The agent loop is unchanged.** Tool failures (including `EOFError`) are surfaced via `_dispatch` exactly as for every other tool — see `agent.py`.
- **The ModelClient Protocol is unchanged.**
- **No per-backend logic.**
- **No new dependencies.**
- **All new tests run offline.**

## Considered and rejected

- **Direct `sys.stdin` / `sys.stdout` inside the handler, no module state.** Simpler in production but makes test isolation worse: when interactive mode is driven via injected `io.StringIO`, the `ask_user` handler reading from real `sys.stdin` would deadlock or hang waiting on a terminal that isn't there. The module-level streams cost ~5 lines and remove that footgun.
- **Passing streams through the tool dispatch path** (`registry.invoke(name, arguments, stdin, stdout)`). Would change the `Tool` handler signature. CLAUDE.md explicitly forbids modifying the Tool contract.
- **Building a per-session `Tool` instance with bound streams** (a `make_ask_user_tool(stdin, stdout)` factory). More flexible than module-level state, but requires the interactive runner to register a fresh tool into the registry at session start. More machinery for no clear gain over the module-level approach for v1.
- **Structured-choice tool** (`ask_user_choice` with a `choices: list[str]` parameter). Useful in production but adds surface for no immediate teaching gain. v1 keeps the design as small as possible; structured choice is in *Future work*.
- **Multi-line input via a sentinel or content-length header.** Adds a small protocol the user has to learn. Deferred until a real need arises.

## Testing strategy

Per CLAUDE.md's tool-test rule, add tests in `tests/test_tools.py` covering at least the happy path and one error case. All offline; no live LLM.

- **Happy path.** `configure_streams(io.StringIO("yes please\n"), io.StringIO())`; invoke handler with `{"question": "go ahead?"}`; assert result is `"yes please"`; assert the output stream contains both the `[ask_user]` line and the `> ` prompt.
- **Trailing newline stripped.** Input `"answer\n"` → result `"answer"`.
- **EOF.** Empty input stream → handler raises `EOFError`. Through the registry, this surfaces as an `error: EOFError: …` tool result — covered by an end-to-end test that scripts a `FakeClient` to call `ask_user` against an empty input stream and verifies the error string reaches the next `model_call`'s messages.
- **Integration with the loop.** A `FakeClient` scripted to (a) call `ask_user`, then (b) emit `final_text` referencing the user's answer. Drive a single `run_agent` call against an input stream containing the canned response. Assert the final text is what the second scripted response says.

A separate per-tool test file (`tests/test_ask_user.py`) is also acceptable if the tool tests grow beyond a few cases — the existing convention is `tests/test_tools.py` for all tools collectively. Stick with the collective file for v1.

## Known risks

These are real and worth surfacing for faculty reviewing the design — the spec doesn't paper over them:

- **Open-weights model variance on Rosie.** The agent decides when to call `ask_user`. CLAUDE.md notes Rosie's model "changes over time"; whether a given Rosie checkpoint reliably invokes the tool when it should is genuinely unknown. The Anthropic backend (Sonnet) is the reliability baseline. This is consistent with CLAUDE.md's "differences in output are data" stance — don't paper-over Rosie behavior with per-backend prompts.
- **Infinite-ask loops.** A misbehaving model could call `ask_user` every turn and never finish. Bounded only by `MAX_ITERATIONS = 25` per `Conversation.send()`. The user can also force exit with Ctrl-D (handler raises `EOFError`, loop continues, model produces a final). Acceptable for v1.
- **Tool-result framing for free-form text.** Tool results are typically deterministic facts ("file read", "command output"). A natural-language user response is a semantic stretch — but it's the same stretch Claude Code's own `AskUserQuestion` makes. Worth a sentence in faculty workshop materials when introducing the tool.

## Future work

When and if real needs arise, not before:

- `ask_user_choice` — structured-choice variant accepting `choices: list[str]`.
- Multi-line input via a sentinel or terminator line.
- Per-call timeout (`timeout_seconds` parameter; handler races readline against a timer).
- A response-validation parameter (regex / type).
- A `SPEC-tools.md` umbrella spec for the tool layer if elicitation-shaped tools become a category.
