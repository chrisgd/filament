# Filament — Known Issues

Tracked issues from the design review on 2026-05-21. Each entry is self-contained
so it can be picked up and fixed in a fresh context window. When an issue is
fixed, change its status to **Done** and note the commit.

Read `CLAUDE.md` before touching anything — the load-bearing constraints there
(don't modify the `Tool` contract or the `ModelClient` Protocol, one tool per
module, no tool-specific logic in the loop, no per-backend prompt tuning) apply
to every fix below.

---

## Issue 1 — `run_shell` mangles output when stdout has no trailing newline

**Status:** Done (commit 95c2744)
**Severity:** Bug (low)
**Location:** `filament/tools/run_shell.py:26-30`

### Problem
The success path concatenates two f-strings with no separator between them:

```python
return (
    f"exit code: {completed.returncode}\n"
    f"--- stdout ---\n{completed.stdout}"
    f"--- stderr ---\n{completed.stderr}"
)
```

If `completed.stdout` does not end in a newline, the `--- stderr ---` header
runs onto the last line of stdout, producing output like
`...last line of stdout--- stderr ---`. This corrupts the transcript and
confuses the model reading the tool result.

### Fix
Insert a newline between the stdout section and the `--- stderr ---` header.
Make the separation unconditional so the format is stable regardless of whether
stdout/stderr end in newlines.

### Acceptance criteria
- A command whose stdout has no trailing newline still renders `--- stderr ---`
  on its own line.
- Add a test in `tests/test_tools.py` covering a command with no-newline stdout.
- Test runs offline (no LLM).

---

## Issue 2 — `run_shell` uses a different error convention than the other tools

**Status:** Done (commit ca4b17d)
**Severity:** Design inconsistency (low)
**Location:** `filament/tools/run_shell.py:24-25`

### Problem
`read_file` and `write_file` *raise* exceptions on failure and let
`agent._dispatch` (`filament/agent.py:77-88`) convert them to an `error: ...`
string. `run_shell` instead *returns* a plain string on timeout:

```python
except subprocess.TimeoutExpired:
    return f"command timed out after {_TIMEOUT_SECONDS}s: {command}"
```

Two error conventions across three tools is a legibility cost in a codebase
whose whole purpose is to be a clear teaching example.

### Fix
Make `run_shell` consistent with the other tools: raise on timeout (e.g. raise
a `TimeoutError` with the same message) and let `_dispatch` surface it. Confirm
`_dispatch`'s generic `except Exception` already catches it — it does, so no
loop changes are needed.

### Acceptance criteria
- All three tools follow the same error convention (raise, don't return).
- The timeout path still produces a readable message for the model.
- Update/keep the timeout test in `tests/test_tools.py`; test runs offline.

---

## Issue 3 — System prompt is embedded in a user message instead of a system role

**Status:** Done (commit bc3f255)
**Severity:** Design weakness (medium)
**Location:** `filament/agent.py:38-40`

### Problem
The loop prepends the system prompt to the first user message:

```python
messages: list[Message] = [
    Message(role="user", content=f"{SYSTEM_PROMPT}\n\nTask: {task}")
]
```

Both backends have a dedicated channel for system instructions, and Filament is
teaching infrastructure *about agent design*. Students should see the system
prompt go where system prompts go. Burying it in a user turn obscures a
teachable abstraction.

### Fix (proposed — confirm approach before implementing)
- Emit a separate `Message(role="system", content=SYSTEM_PROMPT)` as the first
  message, and a `Message(role="user", content=f"Task: {task}")` as the second.
- Translate `role="system"` in each model client — this is exactly the
  wire-format difference clients are meant to absorb:
  - **Rosie** (`model_clients/rosie.py`): emit a `{"role": "system", ...}` entry
    in the `messages` array (OpenAI format).
  - **Anthropic** (`model_clients/anthropic.py`): lift system messages out of
    the `messages` array into the top-level `system` request parameter (the
    Messages API does not accept a `system` role inside `messages`).
- Do NOT modify the `Message` dataclass shape or the `ModelClient` Protocol.
  `role` is already a free `str`; no contract change is required.
- Interaction with caching: on the Anthropic side the `system` parameter is a
  cacheable block. Top-level automatic `cache_control` (already in place at
  `anthropic.py:38`) continues to work; no change needed there.

### Acceptance criteria
- The system prompt reaches Anthropic via the top-level `system` param and
  Rosie via a `system`-role message.
- The agent loop contains no backend-specific logic (constraint from CLAUDE.md).
- `tests/test_model_clients.py` / `tests/test_agent.py` cover the translation;
  unit tests run offline.

---

## Issue 4 — Empty model output silently terminates the loop

**Status:** Done (commit a3e0160)
**Severity:** Bug (medium)
**Location:** `filament/model_clients/rosie.py:99`, `filament/model_clients/anthropic.py:127`, `filament/agent.py:48`

### Problem
Both clients coerce missing/empty content to an empty string:

- Rosie: `return Response(final_text=message.get("content") or "")`
- Anthropic: `return Response(final_text="".join(text_parts))`

The loop terminates on `if response.final_text is not None`. A model turn that
returns no tool calls and no text yields `final_text=""`, which is not `None`,
so the loop ends and returns an empty answer — indistinguishable from a real
completion. This is a confusing failure mode, especially when comparing
backends (e.g. debugging Rosie's open-weights model).

### Fix (decide approach before implementing)
Two reasonable options — pick one and keep it consistent across both clients:
1. Treat genuinely empty output as a distinct condition: return a `Response`
   with neither `final_text` nor `tool_calls`, and have the loop detect it and
   emit an explicit message (e.g. `"Stopped: model returned empty output."`).
2. Keep returning `final_text` but make the empty case an explicit, visible
   sentinel string rather than `""`.

Prefer option 1 — it keeps the `Response` "either/or" semantics honest and
makes the failure observable in the transcript. Coordinate with Issue 6 (the
`Response` invariant), since "neither field set" becomes a meaningful state.

### Acceptance criteria
- An empty model turn produces a visibly distinct outcome, not a silent empty
  answer.
- Both clients behave the same way.
- Covered by an offline test in `tests/test_model_clients.py` or
  `tests/test_agent.py`.

---

## Issue 5 — `Registry.schemas()` is misnamed

**Status:** Done (commit 3d874c9)
**Severity:** Naming (low)
**Location:** `filament/tools/base.py:40-42`, callers in `filament/agent.py:41`

### Problem
`schemas()` returns `list[Tool]` — full `Tool` objects, not schemas. The actual
JSON Schema lives in `Tool.parameters`, and the wire-format schema is built by
each client's `_to_wire_tool`. The name tells a reader something untrue.

### Fix
Rename `Registry.schemas()` to `Registry.tools()` (or similar honest name) and
update the caller in `agent.py`. Pure rename, no behavior change. Check
`tests/` for any reference to `.schemas()` and update.

### Acceptance criteria
- Method name reflects that it returns `Tool` objects.
- All callers and tests updated; `pytest` passes.

---

## Issue 6 — `Response` "either/or" invariant is documented but unenforced

**Status:** Done (commit 6f80e1f)
**Severity:** Robustness (low)
**Location:** `filament/types.py:40-49`

### Problem
The `Response` docstring states it carries "either `final_text` or
`tool_calls`, never both," but nothing enforces it. A client bug producing both
(or, depending on Issue 4's resolution, neither) would pass silently.

### Fix
Add a `__post_init__` to `Response` that asserts the invariant. Coordinate with
Issue 4: if Issue 4 makes "neither field set" a legitimate state (empty model
output), the invariant becomes "not both" rather than "exactly one" — settle
Issue 4 first, then write `__post_init__` to match the final agreed semantics.

### Acceptance criteria
- Constructing a `Response` that violates the agreed invariant raises.
- The invariant in the docstring and the `__post_init__` check agree.
- Offline test in `tests/` covers the violation.

---

## Issue 7 — Commented-out alternative system prompt breaks if uncommented

**Status:** Done (commit 6b536a7)
**Severity:** Bug (low) — latent
**Location:** `filament/agent.py:22-32`

### Problem
The commented-out alternative `SYSTEM_PROMPT` is intentional instructional
content: it shows readers a more robust prompt than the active one. That is
fine and should stay. But as written, if a reader uncomments it, adjacent
string literals concatenate with no separating space:

- `"...the provided tools."` + `"Read before you write:..."` → `"...tools.Read before..."`
- `"...making changes."` + `"Stay within the scope..."` → `"...changes.Stay within..."`
- `"...retry the same call."` + `"You are done..."` → `"...call.You are done..."`

The active `SYSTEM_PROMPT` above gets this right (trailing space on each
continued line); the commented block does not.

### Fix
Add a trailing space to the end of each line in the commented block that is
followed by another literal, matching the active prompt's style. Leave the
wording of the prompt otherwise unchanged. Also replace the current
`# Longer system prompt, but less clear to someone reading the code` line —
which reads as a criticism — with a line stating the block's purpose, e.g.
`# Alternative, more robust system prompt, kept as an instructional example.`

### Acceptance criteria
- Uncommenting the block yields a prompt with correct word spacing throughout.
- A comment above the block explains it is an instructional example of a more
  robust prompt.
- The wording of the prompt text itself is unchanged.

---

## Issue 8 — `--backend` help text claims a default the flag does not have

**Status:** Done (commit 05f1caf)
**Severity:** Doc/UX (low)
**Location:** `filament/cli.py:30`

### Problem
The `--help` text for `--backend` says "defaults to anthropic":

```python
help="override the FILAMENT_BACKEND setting for this run, defaults to anthropic",
```

The `--backend` argument has no argparse default — it is `None` unless passed,
and only overrides `config.backend` when set (`cli.py:36-37`). The "anthropic"
default actually lives in `config.py` / `FILAMENT_BACKEND`. As worded, the help
implies the flag itself defaults to anthropic, which is misleading.

### Fix
Either drop the clause, or attribute the default correctly, e.g.
`"override the FILAMENT_BACKEND setting for this run (which defaults to anthropic)"`.

### Acceptance criteria
- Help text does not imply the `--backend` flag has an argparse default.
- If the default is mentioned, it is attributed to the `FILAMENT_BACKEND`
  setting, not the flag.

---

## Issue 9 — `main()` reports only configuration errors; runtime failures escape as a traceback

**Status:** Done (commit ad80ef7)
**Severity:** Bug (medium)
**Location:** `filament/cli.py:39-58`

### Problem
`main()` catches `ValueError` from `build_client` and prints a clean
`configuration error: ...` line (`cli.py:42-44`). But the `run_agent` call at
`cli.py:54` is wrapped only in `try/finally` — there is no `except`. Any failure
raised inside the agent loop escapes `main()` as an unhandled traceback:

- `httpx` transport errors — connection refused, DNS failure, read timeout
- `reply.raise_for_status()` on a 401/429/500 (`anthropic.py:55`, `rosie.py:44`)
- the 120s request timeout (`_TIMEOUT_SECONDS = 120.0`) — before it fires, the
  CLI sits completely silent, then throws

The result is an inconsistent failure surface: a missing API key produces a
tidy one-liner, but a backend that is down, rate-limiting, or slow produces
either a two-minute silent hang or a raw Python traceback. For teaching
infrastructure that is meant to be legible, neither is acceptable. Note also
that all error output goes to stderr — only a successful `print(result)` writes
to stdout — so any invocation that captures only stdout makes every failure
mode look identical and silent.

### Fix
Wrap the `run_agent` call so runtime failures are reported as a clean
`error: ...` line on stderr and `main()` returns a nonzero exit code. Catch
`httpx.HTTPError` explicitly (it covers both transport and status errors); decide
whether to also broadly catch other exceptions or let genuinely unexpected ones
surface. Keep the existing `ValueError` config-error handling and keep
`session.close()` in the `finally`. Do **not** add error handling inside the
loop — `agent._dispatch` already turns *tool* failures into text; this issue is
about failures of the model call and transport, which belong to the CLI.

### Acceptance criteria
- A backend or transport failure produces a one-line `error: ...` message and a
  nonzero exit code, not a traceback.
- The session transcript is still closed on the failure path.
- Covered by an offline test (see Issue 10).

---

## Issue 10 — The CLI layer has no test coverage

**Status:** Done (commit ad80ef7)
**Severity:** Test gap (medium)
**Location:** `tests/` (no `test_cli.py`); `filament/cli.py`

### Problem
Every module has a matching test file except `cli.py`: `tests/` contains
`test_agent.py`, `test_tools.py`, `test_session.py`, and `test_model_clients.py`,
but no `test_cli.py`. `main()`, argument parsing, runtime assembly, and the
error-printing branches are never exercised. `test_agent.py` calls `run_agent`
directly with fake clients (`test_agent.py:13,47`), bypassing the entire CLI
layer. As a result no test asserts behavior for missing arguments, an invalid
`--backend`, a missing API key, or a backend failure — which is why Issue 9 went
unnoticed.

### Fix
Add `tests/test_cli.py`. `main()` already takes an explicit `argv` argument
(`cli.py:20`) specifically so it can be driven from a test — use it. Cover at
minimum:

- No arguments: argparse raises `SystemExit(2)` (assert with `pytest.raises`).
- Configuration error: a missing API key yields exit code 2 and a message on
  stderr.
- Happy path: with a stubbed/fake client and registry, `main()` returns 0 and
  prints the agent result to stdout.
- `--backend` overrides `config.backend`.

Stub `build_client` / `run_agent` or inject fakes — tests must not hit a real
backend (CLAUDE.md: tests run offline). Nothing here is an integration test.

### Acceptance criteria
- `tests/test_cli.py` exists and drives `main()` via its `argv` parameter.
- Missing-args, config-error, and happy paths are all asserted (exit codes and
  output streams).
- Tests run offline under the default `pytest` run.

---

## Issue 11 — `filament` with no arguments prints a bare argparse error instead of help

**Status:** Done (commit 13e85fb)
**Resolution:** A third option beyond the two in the original issue — no-args launches interactive mode. See @specs/SPEC-interactive.md.
**Severity:** UX (low)
**Location:** `filament/cli.py:20-32`

### Problem
Running `filament` with no arguments prints argparse's two-line usage error to
stderr and exits 2:

```
usage: filament [-h] [--backend {rosie,anthropic}] task
filament: error: the following arguments are required: task
```

For a one-shot teaching CLI, a new user's first instinct is often to run the
bare command to see what it does; a terse "argument required" error on stderr is
a thin first impression, and easy to miss entirely if stderr is not visible.
Many CLIs print full help in this case. This is minor and partly a matter of
taste — flagging it for an explicit decision, not mandating a change.

### Fix (optional — decide before implementing)
If `argv` is empty, print `parser.format_help()` to stdout and exit. Keep the
argparse error for the case where some arguments are given but `task` is
missing. Do not over-engineer this.

### Acceptance criteria
- A decision is recorded: either implement no-args-prints-help, or move this
  entry to "Resolved / Not an issue" with a one-line rationale.

---

## Issue 12 — `Conversation.send()` leaves messages malformed on empty-output and iteration-cap exits

**Status:** Done (commit eabdb0d)
**Severity:** Bug (medium)
**Location:** `filament/agent.py:96-97, 119-122`

### Problem
`Conversation.send()` has three exit paths. The `final_text` path correctly
appends an assistant message to `self.messages` before returning
(`agent.py:87-94`), so the next `.send()` sees a coherent history. The other
two paths do not:

- **Empty-output** (`agent.py:96-97`): returns the `"Stopped: model returned
  empty output."` sentinel without appending anything. `self.messages` ends
  with the user message and no assistant reply.
- **`MAX_ITERATIONS` cap** (`agent.py:119-122`): returns the
  `"Stopped: reached the 25-iteration limit..."` sentinel without appending
  anything. `self.messages` ends with the most recent tool_result.

For one-shot use (`run_agent`) this is invisible — `messages` is discarded on
return. But interactive mode persists the `Conversation` across turns. After
either exit, a follow-up `.send("next task")` produces a history like
`[system, user, user]` (empty-output case) or `[..., tool, user]`
(cap case). Some backends reject those shapes outright; others happily reply
to a malformed conversation, masking the bug.

This is the same bug-class that was caught and fixed in PR #6 for the
`final_text` path. The fix should have been generalized; it wasn't.

### Fix
Append a synthetic `Message(role="assistant", content=<sentinel>)` immediately
before returning on each of the two remaining exit paths, using the same
sentinel string that is returned. Two lines per path. No other changes.

### Acceptance criteria
- After a `.send()` that returns the empty-output sentinel, the next
  `.send("anything")` sees a `[system, user, assistant, user]` history.
- Same for the iteration-cap sentinel.
- Multi-turn test in `tests/test_agent.py` covers both paths.
- Existing single-turn tests of `run_agent` continue to pass untouched.

---

## Issue 13 — Interactive read-loop fails to strip `\r`, breaking CRLF slash commands

**Status:** Done (commit 5da7c1e)
**Severity:** Bug (low)
**Location:** `filament/interactive.py:68`

### Problem
The read-loop strips only `\n` from each line:

```python
line = line.rstrip("\n")
```

If input arrives with CRLF line endings (e.g. `printf "/exit\r\n" | filament`,
or input piped from a file edited on Windows or by a tool that writes CRLF),
`/exit` becomes `/exit\r`, fails the exact-match check against `_COMMANDS`,
and is treated as a task — sent to the model. Same for `/reset`, `/messages`,
`/help`. The user sees the agent attempt to "do" their command instead of
exiting/resetting.

This was considered during PR #6 implementation and dismissed as
"Mac/Linux infra only." That dismissal was wrong — the piped-input case is
real on Unix too, and the fix is a single character.

### Fix
Change `line.rstrip("\n")` to `line.rstrip("\r\n")`. Both characters are
trimmed; LF-only input is unaffected.

### Acceptance criteria
- An interactive session driven with `\r\n`-terminated input exits cleanly
  on `/exit\r\n` and resets cleanly on `/reset\r\n`.
- Test in `tests/test_interactive.py` exercises CRLF input.
- LF-only behavior is unchanged.

---

## Issue 14 — Malformed tool-call JSON from the backend escapes as a traceback

**Status:** Done (commit 5ff3183)
**Severity:** Bug (medium)
**Location:** `filament/model_clients/rosie.py:100`, `filament/cli.py:76`, `filament/interactive.py:170`

### Problem
`rosie.py` parses each tool call's `arguments` string with `json.loads`.
Open-weights models under vLLM emit truncated or invalid argument JSON often
enough to matter. The resulting `JSONDecodeError` is not an `httpx.HTTPError`,
so neither the one-shot CLI handler nor the interactive read-loop catches it:
one-shot exits with a traceback, and an interactive session dies mid-turn,
taking its conversation with it.

### Fix
Add a Filament-level `ModelResponseError` in `filament/model_clients/base.py`,
raised by a client when the backend's reply cannot be translated into internal
types. Raise it from the Rosie client on malformed or non-object tool-call
arguments. Catch it alongside `httpx.HTTPError` in `cli.py` and
`interactive.py` so it renders as the same `error: <Type>: <message>` line.
The `ModelClient` Protocol's signature is unchanged.

### Acceptance criteria
- Malformed tool-call JSON produces `error: ModelResponseError: ...` and exit
  code 1 in one-shot mode; in interactive mode the loop continues.
- Offline tests in `tests/test_model_clients.py`, `tests/test_cli.py`, and
  `tests/test_interactive.py`.

---

## Issue 15 — HTTP status errors discard the backend's explanation

**Status:** Done (commit 274bdf5)
**Severity:** UX (low)
**Location:** `filament/cli.py:76`, `filament/interactive.py:170`

### Problem
Both clients call `reply.raise_for_status()`. The resulting `HTTPStatusError`
message carries only the status code and URL (`Client error '400 Bad Request'
for url ...`). The response body, where both Anthropic and vLLM put the actual
reason (invalid model, malformed request, exhausted credit), is dropped. That
is the one line a faculty member debugging a backend needs.

### Fix
At both catch sites, when the exception is an `httpx.HTTPStatusError`, also
print the response body to stderr. Print the raw text; do not parse
provider-specific error shapes.

### Acceptance criteria
- A non-2xx reply prints the body after the `error:` line in both modes.
- Transport errors (no response) are unchanged.
- Offline tests in `tests/test_cli.py` and `tests/test_interactive.py`.

---

## Issue 16 — The model's text is dropped when a turn also carries tool calls

**Status:** Done (commit ef339de)
**Severity:** Design weakness (medium)
**Location:** `filament/types.py:53`, `filament/model_clients/anthropic.py:143`, `filament/model_clients/rosie.py:95`

### Problem
Both wire formats let one turn carry text and tool calls together, and
models use that constantly: "Let me read the README first" followed by the
call. `Response` forbids the combination (issue 6 enforced it), so both
clients discard the text whenever tool calls are present. It never reaches
the transcript, the next model call, or the user. The replayed conversation
is also unfaithful: the assistant turn the backend sees on the next call is
missing words it actually said. `Message` already supports content alongside
`tool_calls`, and the Anthropic client's text-plus-`tool_use` branch at
`anthropic.py:100` is dead code because nothing can produce that shape.

For a harness whose purpose is to make the loop legible, the interstitial
text is the most legible thing the model does.

### Fix
Rename `Response.final_text` to `text` and drop the either/or invariant: a
turn with tool calls is not final whatever its text; a turn with neither is
empty output. Both clients keep the text. The loop records it on the
assistant message beside the tool calls; its control flow is otherwise
unchanged. The transcript's `model_response` event carries `text` instead of
`final_text`.

### Acceptance criteria
- A turn with text and tool calls stores both on the assistant message; the
  next `model_call` sees the text; the transcript records it.
- Both clients preserve the text; the Anthropic client renders the assistant
  text block before its `tool_use` blocks on replay.
- Existing loop and interactive tests pass after the rename.

---

## Issue 17 — Output cut off at the token limit is indistinguishable from a complete answer

**Status:** Done (commit f4643c3)
**Severity:** Bug (medium)
**Location:** `filament/model_clients/anthropic.py:128`, `filament/model_clients/rosie.py:91`, `filament/agent.py`

### Problem
Neither client reads the backend's stop reason (`stop_reason` on Anthropic,
`finish_reason` on Rosie). A reply cut off at `max_tokens` (4096 in the
Anthropic client) comes back as ordinary text and the loop treats it as a
finished answer. Worse, a `write_file` call whose body exceeds the cap loses
its `tool_use` block, and the loop then reports "model returned empty
output", sending the student to debug the wrong thing.

### Fix
Add `Response.truncated: bool`, set by each client from its backend's stop
reason (`max_tokens` / `length`). A truncated turn carries only its text;
the client does not parse tool calls that may have been cut mid-way. The
loop checks the flag before anything else and stops with an explicit
sentinel, keeping any partial text so the user sees what came back.

### Acceptance criteria
- A truncated turn produces a visibly distinct result and is never
  dispatched.
- The transcript's `model_response` event records `truncated`.
- Both clients map their stop reason; offline tests cover both.

---

## Issue 18 — Activity lines break on newlines in argument values

**Status:** Done (commit c08ee49)
**Severity:** Bug (low)
**Location:** `filament/interactive.py:81` (`_format_args`)

### Problem
`_format_args` stringifies each argument value as-is before truncating. A
`write_file` call whose `content` has a newline in its first 60 characters
produces a `[tool]` line that spans several terminal lines, against the
activity-signals spec's one-line-per-event rule. Only the echoed line is
affected; the full value is in the transcript.

### Fix
Escape `\n` and `\r` in the rendered value before truncating, so one event
stays one line and the truncation width is measured on what is printed.

### Acceptance criteria
- A multi-line argument value renders as one `[tool]` line with the line
  break shown as `\n`.
- Test in `tests/test_interactive.py`.

---

## Issue 19 — Anthropic default is Sonnet 4.6; Claude 5 models need thinking-block replay first

**Status:** Deferred (decision 2026-09-02); see @specs/SPEC-provider-state.md
**Severity:** Design decision
**Location:** `filament/config.py:14`, `filament/model_clients/anthropic.py`

### Problem
Sonnet 5 is newer and cheaper than the current default, but every Claude 5
model runs thinking by default and requires its thinking blocks to be
replayed unchanged on the next turn. The Anthropic client has nowhere to
keep them, so switching the default string alone would fail on the second
turn of any tool-use conversation.

### Resolution
Keep `claude-sonnet-4-6`. Implement the provider-state replay spec first;
then the default flips to `claude-sonnet-5` in one line. The discussion,
the design, and the alternatives considered are recorded in the spec.

---

## Resolved / Not an issue

### Explanatory comments in `cli.py` `main()` — INTENTIONAL, no action
The step-by-step comments added in `cli.py` `main()` (`# loads up the
configuration`, `# create the client`, etc.) were flagged in review as
low-value narration. They are intentional: they exist for human readers
skimming the code, who benefit from signposting more than an AI reader would.
This is teaching infrastructure — keep them. Do not re-flag in future reviews.

### cache_control at the Anthropic request top level — CORRECT, no action
`filament/model_clients/anthropic.py:38` sets `"cache_control": {"type":
"ephemeral"}` at the top level of the request body. This was initially flagged
as a no-op, but the current Anthropic Messages API documentation confirms it is
the supported **automatic caching** mode: a single top-level `cache_control`
field makes the API apply a cache breakpoint to the last cacheable block and
advance it as the conversation grows. It is explicitly recommended for
multi-turn conversations, which is exactly Filament's agent loop. The code is
correct as written — no change needed. (Verified 2026-05-21 against
platform.claude.com prompt-caching docs.)
