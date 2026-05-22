# Filament — Build Spec

A minimal agent harness in Python that runs against either Rosie's vLLM endpoint or the Anthropic API.

This document is the build spec. The accompanying `CLAUDE.md` is the project memory that will live in the repo going forward. Build the project as specified here, following the principles in `CLAUDE.md`.

---

## What Filament is

Filament is a small Python CLI that takes a task description in natural language, sends it to an LLM, and lets the model accomplish the task by calling registered tools. It is the minimum viable shape of a coding agent: a model, a tool registry, an agent loop, and a session log.

It supports two backends — Rosie (running an open-weights model under vLLM with an OpenAI-compatible API) and Anthropic's Messages API — selected at runtime via configuration. The same task prompt runs against both without modification.

It is *not*:

- Claude Code or a clone of it. No plan mode, no subagents, no MCP, no patch-mode file editing primitives.
- A research framework. No novel architecture; the design is deliberately conventional.

---

## Architecture

The codebase has four layers. Each must be cleanly separated from the others.

**CLI layer** (`filament/cli.py`)
Parses arguments, loads config, constructs the model client and tool registry, hands them to the agent loop, prints the final result. The CLI knows nothing about HTTP, tool implementation details, or backend wire formats.

**Agent loop** (`filament/agent.py`)
The read/decide/act/observe cycle. Operates entirely on Filament's internal types — never sees raw API responses or backend-specific message formats. Takes a task string, calls the model client with the current internal-format message history and the registry's tool definitions, dispatches any tool calls through the registry, appends results to the history, repeats until the model returns a final response.

**Model clients** (`filament/model_clients/`)
A small package containing one client per backend. Each client implements the same protocol: `complete(messages, tools) -> Response`. Each translates between Filament's internal types and its backend's wire format. The agent loop selects a client at construction time and never branches on which one it has.

**Tool layer** (`filament/tools/`)
A registry plus individual tool modules. Each tool is its own module exporting a `Tool` instance. The registry collects tools and exposes them to the agent loop via two methods: `schemas()` returns the list of tool definitions in Filament's internal format, and `invoke(name, arguments)` dispatches a call.

A separate `filament/session.py` handles transcript logging — writing a structured record of every model call, tool call, and tool result to a session file. The agent loop calls into it; the loop itself stays focused on control flow.

---

## Internal types

This is the seam that makes dual-backend support clean. The agent loop, tool registry, and session module all operate on these types. Only the model clients translate to and from backend-specific wire formats.

Define these in `filament/types.py` as plain dataclasses:

**`Message`** — One turn in the conversation history. Fields:
- `role` — one of `"user"`, `"assistant"`, `"tool"`
- `content` — string content, or `None` if the message is purely tool calls
- `tool_calls` — list of `ToolCall` objects, or `None`; only meaningful for assistant messages
- `tool_call_id` — string identifying which tool call this message is the result of; only meaningful for tool-role messages
- `name` — name of the tool that produced the result; only meaningful for tool-role messages

**`ToolCall`** — A request from the model to invoke a tool. Fields:
- `id` — a unique identifier the backend uses to correlate calls with results
- `name` — the tool's name
- `arguments` — a dict of argument names to values, already parsed (not a JSON string)

**`Response`** — What a model client returns from `complete`. Fields:
- `final_text` — the model's text response if it's done, otherwise `None`
- `tool_calls` — list of `ToolCall` objects if the model wants to call tools, otherwise empty list

A response has either `final_text` or `tool_calls`, never both. The agent loop uses this to decide whether to dispatch tools and continue, or return to the caller.

---

## The Tool contract

This is an *important* architectural commitment.

A Tool is a dataclass with four fields: `name` (string), `description` (string, used by the model to decide when to call it), `parameters` (a JSON Schema dict describing the arguments), and `handler` (a callable that takes a dict of arguments and returns a string result).

Tools live in `filament/tools/`, one module per tool. Each module exports a single `Tool` instance. The package's `__init__.py` imports them and registers them with the registry. Adding a new tool means: (1) creating a new module in `filament/tools/`, (2) exporting a `Tool` instance from it, (3) importing and registering it in `__init__.py`, (4) writing tests in `tests/test_tools.py`.

The agent loop must never be modified to add a tool. Tools must never be invoked directly from anywhere except the registry.

---

## Model client protocol

Both clients implement the same minimal interface, defined as a Protocol in `filament/model_clients/base.py`:

```python
class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[Tool]) -> Response: ...
```

Both clients take Filament's internal types and return `Response`. Each translates internal types to its backend's wire format on the way out, and the backend's response back to a `Response` on the way in. Neither client leaks its wire format to anything else in the codebase.

Concretely:

**Rosie client** (`filament/model_clients/rosie.py`) — `POST {endpoint}/chat/completions` with the standard OpenAI request body. Tools are wrapped in the `{"type": "function", "function": {...}}` envelope. Tool call results in the message history are role `"tool"` with `tool_call_id`. Returns `Response` extracted from `choices[0].message`.

**Anthropic client** (`filament/model_clients/anthropic.py`) — `POST https://api.anthropic.com/v1/messages` with the standard Messages API request body. Required headers: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`. Tools are passed as `{"name": "...", "description": "...", "input_schema": {...}}`. Tool call results go in a user-role message whose `content` is a list containing `{"type": "tool_result", "tool_use_id": "...", "content": "..."}` blocks. Returns `Response` extracted from the content blocks of the response (text blocks become `final_text`, tool_use blocks become `tool_calls`).

Both use `httpx` directly. No SDK dependencies. The wire format being visible in the client code is a deliberate teaching choice.

---

## Backend selection

The CLI constructs one client at startup based on configuration:

- If `FILAMENT_BACKEND=rosie` (default), construct the Rosie client using `FILAMENT_ROSIE_ENDPOINT` and `FILAMENT_ROSIE_MODEL`.
- If `FILAMENT_BACKEND=anthropic`, construct the Anthropic client using `FILAMENT_ANTHROPIC_API_KEY` and `FILAMENT_ANTHROPIC_MODEL`.
- A `--backend rosie|anthropic` CLI flag overrides the env var.

A factory function in `filament/model_clients/__init__.py` does this selection. The agent loop receives a ready-constructed client and does not know which backend is behind it.

The system prompt and the task prompt are identical regardless of backend. No per-backend prompt tuning. If a backend handles a prompt poorly, that is data worth surfacing rather than papering over. This matters specifically because the open-weights model on Rosie may change over time, and we want the comparison surface to remain stable.

---

## Initial tools to implement

Three tools, enough to make the harness useful as a starting point.

**`read_file`** — Takes a `path` argument. Returns the file's contents as a string. Raises a clear error if the file is missing or unreadable. No path restrictions in this version; the agent is trusted within the workshop's scope.

**`write_file`** — Takes `path` and `content` arguments. Writes content to the path, creating parent directories as needed. Returns a confirmation string. Overwrites existing files.

**`run_shell`** — Takes a `command` argument. Runs the command via subprocess with a 30-second timeout. Returns combined stdout + stderr as a string. Includes exit code in the returned string so the model can see whether the command succeeded.

Each tool gets its own test file or test section. Tests should cover the happy path plus at least one error case, and should not require the LLM to run.

---

## Configuration

Settings load from environment variables, with defaults in `filament/config.py`:

- `FILAMENT_BACKEND` — `rosie` or `anthropic`. Default: `rosie`.
- `FILAMENT_ROSIE_ENDPOINT` — base URL for Rosie's OpenAI-compatible API.
- `FILAMENT_ROSIE_MODEL` — model name to request from Rosie.
- `FILAMENT_ANTHROPIC_API_KEY` — Anthropic API key.
- `FILAMENT_ANTHROPIC_MODEL` — Anthropic model name. Default: a current Sonnet variant (`claude-sonnet-4-5` or equivalent). Sonnet, not Opus — the workshop wants a fair comparison against an open-weights model on Rosie, not a stacked deck.

The CLI accepts `--backend` as an override but no other CLI flags for these settings in v1. Faculty changing endpoints or models do so via environment.

---

## CLI shape

```
filament "task description here"
filament --backend anthropic "task description here"
```

The CLI prints the agent's final response to stdout. The session transcript is written to `./filament-sessions/{timestamp}.jsonl` and includes which backend was used for the run. Each line of the transcript is a JSON object describing one event: a model call (with the full internal-format message list sent, plus which backend was selected), a model response, a tool call, or a tool result.

---

## What to build

Build the project in this order. Each step's tests pass before moving to the next.

1. Project structure, `pyproject.toml`, `README.md`.
2. Internal types (`filament/types.py`). No tests required — these are just dataclasses — but they must be defined first since everything depends on them.
3. The Tool dataclass, the registry, and the three initial tools, with tests. Foundation everything else depends on. Tests must not require the LLM.
4. The session transcript module, with tests.
5. The model client `Protocol` in `filament/model_clients/base.py`.
6. The Rosie model client, with a contract test that hits the real endpoint with a trivial completion (one user message, no tools, assert a coherent text response comes back). Mark the contract test `@pytest.mark.integration` so it's not run by default.
7. The Anthropic model client, with the same shape of contract test, also marked integration.
8. The client factory in `filament/model_clients/__init__.py`.
9. The agent loop, wiring everything together. Tests for the loop use a fake `ModelClient` (returning canned `Response` objects) — no live LLM required.
10. The CLI.
11. End-to-end smoke tests, one per backend: `filament "read the README and tell me what this project does"` should produce a coherent response with the transcript showing a `read_file` tool call. Run once with `--backend rosie`, once with `--backend anthropic`. These are manual verification, not automated.

Stop after both smoke tests pass. Resist scope creep. No retries-with-different-prompts, no streaming, no fancy output formatting, no telemetry beyond the session log.

---

## Constraints

- Python 3.11+.
- `httpx` for HTTP; no `openai` SDK, no `anthropic` SDK. The wire formats should be visible in the client code for teaching purposes.
- `pytest` for tests.
- Type hints throughout. The model clients, internal types, and registry interfaces should pass `mypy --strict`. The rest of the codebase can be looser but should still be typed where it helps clarity.
- No async in v1. Synchronous everywhere.
- No external dependencies beyond `httpx`, `pytest`, and Python stdlib.
- No per-backend prompt customization. The agent loop sends the same internal-format messages regardless of which client is downstream.

---

## What success looks like

After build, someone reading this codebase for the first time can:

1. Trace a task end-to-end in under five minutes by following the code.
2. Identify where to add a new tool without consulting documentation other than `CLAUDE.md`.
3. Understand the separation between agent loop, model clients, and tool layer.
4. See clearly which parts of the codebase would change when adding a third backend and which would not.
5. Run the tests and have them pass (excluding integration tests that need live endpoints).
6. Run the CLI against either backend and get a coherent response on a simple task.

If all six hold, the codebase is ready for the Thursday workshop.
