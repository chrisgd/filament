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

**Status:** Open
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

**Status:** Open
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

**Status:** Open
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

## Resolved / Not an issue

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
