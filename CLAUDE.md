# Filament

A minimal agent harness for the Diercks School of Advanced Computing. Python CLI that runs a read/decide/act/observe loop against either Rosie (open-weights model via vLLM, OpenAI-compatible API) or the Anthropic API, dispatching tool calls through a registry.

This is teaching infrastructure. It is intentionally small, intentionally conventional, and intentionally extensible. Faculty and summer grant participants will read and modify this code; clarity matters more than cleverness.

## Architecture Overview

```
CLI → Agent Loop → Model Client ─┬─→ Rosie (vLLM, OpenAI-compatible)
         ↓                       └─→ Anthropic Messages API
     Tool Registry → individual Tool modules
         ↓
     Session Log
```

Four layers, cleanly separated:

- **CLI** parses arguments and assembles the runtime; knows nothing about HTTP or tool internals.
- **Agent loop** runs the iteration; operates entirely on Filament's internal types; knows the registry interface and the model client interface, nothing more specific.
- **Model clients** translate between Filament's internal types and a specific backend's wire format. The Rosie client speaks OpenAI's chat-completions format; the Anthropic client speaks the Messages API. Both implement the same `complete(messages, tools) -> Response` interface.
- **Tool layer** owns tool definitions, registration, and dispatch.

A separate session module writes a structured transcript of every event.

## Internal Types

The agent loop, tool registry, and session module operate on Filament's internal types — not on raw API payloads. Backend differences are absorbed in the model clients.

Defined in `filament/types.py`:

- **`Message`** — One turn in conversation history. Has `role`, `content`, `tool_calls`, `tool_call_id`, `name`.
- **`ToolCall`** — A request from the model to invoke a tool. Has `id`, `name`, `arguments` (already-parsed dict).
- **`Response`** — A model client's return value. Has `final_text` or `tool_calls`, never both.

If you find yourself reaching for a raw API response shape outside of a model client, stop and route through the internal types instead.

## Key Design Principles

- **The loop owns control flow; tools own actions.** The agent loop should never contain tool-specific logic. If you find yourself writing `if tool_name == "read_file"` in the loop, you have made an error.
- **Tools are pure functions with declarative schemas.** A tool is a `Tool` dataclass instance with `name`, `description`, `parameters` (JSON Schema), and `handler`. The handler takes a dict and returns a string. Side effects are explicit and documented in the tool's description.
- **One tool per module.** Each tool lives in its own file under `filament/tools/`. The module exports exactly one `Tool` instance. The package `__init__.py` registers them.
- **The registry is the only dispatch path.** Tools are invoked through `registry.invoke(name, arguments)` — never imported and called directly from the loop or the CLI.
- **The agent loop is backend-agnostic.** It speaks only internal types. Model clients absorb wire-format differences. A future faculty member adding a third backend (Gemini, Mistral, a local Ollama instance) writes a new client conforming to the same `Protocol`, and nothing else changes.
- **No per-backend prompt tuning.** The same prompt runs against every backend. If a backend handles it poorly, that's data — don't paper over it. This matters specifically because the open-weights model on Rosie changes over time; the comparison surface should stay stable.
- **Tests do not require the LLM.** Tool tests, registry tests, session tests, and agent loop tests all run offline. Integration tests that hit a real backend are marked `@pytest.mark.integration` and excluded from the default test run.
- **Transcripts are structured, not narrative.** The session log is JSONL with typed events (`model_call`, `model_response`, `tool_call`, `tool_result`). Each model_call event records which backend was used. Faculty can grep, parse, and analyze sessions without reading prose.

## Adding a New Tool

This is the most common extension that will be made. Follow this exact procedure:

1. Create `filament/tools/<your_tool_name>.py`.
2. In that module, define a `Tool` instance with `name`, `description`, `parameters` (a JSON Schema dict), and `handler` (a function taking a dict, returning a string).
3. Import the instance from the module in `filament/tools/__init__.py` and add it to the registry.
4. Add tests in `tests/test_tools.py` covering at least the happy path and one error case. Tests must not require the LLM.

You should **not**:

- Modify `filament/agent.py` to add a tool.
- Invoke the tool's handler from outside the registry.
- Add tool-specific branching anywhere outside the tool's own module.

If a proposed change requires violating any of the above, stop and flag it for human review.

## Adding a New Backend

This is the second most common extension. Follow this procedure:

1. Create `filament/model_clients/<backend_name>.py`.
2. Implement a class with a `complete(self, messages: list[Message], tools: list[Tool]) -> Response` method conforming to the `ModelClient` Protocol in `base.py`.
3. The client translates internal types to the backend's wire format on the way out, and the backend's response back to a `Response` on the way in. Wire-format details must not leak.
4. Add the backend to the factory in `filament/model_clients/__init__.py`, keyed on a new `FILAMENT_BACKEND` value.
5. Add config variables in `filament/config.py`.
6. Write a contract test marked `@pytest.mark.integration` that hits the real backend with a trivial completion.

You should **not** modify the agent loop, the registry, or any tool to accommodate a new backend.

## Tech Stack

- **Python 3.11+**
- **httpx** for HTTP (no `openai` or `anthropic` SDKs; we want both wire formats visible)
- **pytest** for testing
- Standard library for everything else

No async in this version. No external services beyond the configured backend.

## Project Structure

```
filament/
├── cli.py                          # Argument parsing, runtime assembly
├── agent.py                        # The read/decide/act/observe loop + Conversation
├── interactive.py                  # Interactive-mode read-loop (see @specs/SPEC-interactive.md)
├── types.py                        # Message, ToolCall, Response dataclasses
├── config.py                       # Environment-based settings
├── session.py                      # Transcript logging
├── model_clients/
│   ├── __init__.py                 # Factory: picks client by FILAMENT_BACKEND
│   ├── base.py                     # ModelClient Protocol
│   ├── rosie.py                    # OpenAI-compatible client for Rosie
│   └── anthropic.py                # Anthropic Messages API client
└── tools/
    ├── __init__.py                 # Registry and tool registration
    ├── base.py                     # Tool dataclass and Registry class
    ├── read_file.py
    ├── write_file.py
    └── run_shell.py
tests/
├── test_cli.py
├── test_tools.py
├── test_agent.py
├── test_interactive.py
├── test_model_clients.py           # Integration tests, marked @pytest.mark.integration
└── test_session.py
specs/
└── SPEC-interactive.md             # Interactive mode design spec
```

## Development Conventions

- **Type hints throughout.** The model clients, internal types, and registry interfaces should pass `mypy --strict`. The rest of the codebase can be looser but should still be typed where it helps clarity.
- **Tests run on every commit.** `pytest` from the project root. Integration tests that hit a real backend are marked `@pytest.mark.integration` and excluded by default; run them explicitly with `pytest -m integration`.
- **New code lands with tests.** Any new module or user-facing behavior ships with offline tests in `tests/`, not just tools and backends. If a change has no test, that's a blocker, not a follow-up.
- **Commits are small.** A new tool is one commit. A refactor is its own commit. No mixed changes.
- **Never push directly to `main`.** `main` is protected. Do all work on a feature branch and open a pull request; `main` only advances through merged PRs.
- **The README stays minimal.** Installation, one usage example per backend, link to this file. No tutorial content; faculty workshop materials live elsewhere.

## Constraints That Apply to All Changes

- **Do not add features that obscure the agent loop.** Streaming, retries, fancy formatting, telemetry — all of these are valid future work but not yet. The current version's purpose is to be *legible*. A coder should be able to trace a task end-to-end in under five minutes.
- **Do not introduce new dependencies casually.** `httpx` and `pytest` are it. Adding anything else requires explicit human approval.
- **Do not modify the Tool contract or the ModelClient Protocol.** These are the load-bearing abstractions. If a use case seems to require modifying them, the use case probably belongs in a wrapper or a new module.
- **Do not customize prompts per backend.** Same prompt, every backend. Differences in output are data.

## Configuration

Settings load from environment variables with defaults defined in `filament/config.py`:

- `FILAMENT_BACKEND` — `anthropic` (default) or `rosie`
- `FILAMENT_ROSIE_ENDPOINT` — base URL for Rosie's OpenAI-compatible API
- `FILAMENT_ROSIE_MODEL` — model name to request from Rosie
- `FILAMENT_ANTHROPIC_API_KEY` — Anthropic API key
- `FILAMENT_ANTHROPIC_MODEL` — Anthropic model name; defaults to a current Sonnet variant

The CLI accepts `--backend rosie|anthropic` as an override. Faculty running Filament against different Rosie partitions or different Anthropic models change settings via environment, never via code edits.

## Key References

- `SPEC.md` — Original build spec (kept for reference; this CLAUDE.md is now the authority)
- @specs/SPEC-interactive.md — Interactive mode design spec; read this before touching interactive-mode code
- `tests/` — The behavioral contract; if in doubt, read the tests
