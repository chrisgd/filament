# Filament — Classroom Layer

A design spec for turning Filament into a tool students use for coursework, without giving up the legible core that faculty teach from. This is a component spec; the load-bearing constraints in `CLAUDE.md` apply to the core. This spec defines where they stop applying and what replaces them.

**Status: proposed.** Written 2026-10-12 to record a design discussion and the decisions that came out of it.

## Decision record

- 2026-10-12: Filament will be used by students as a working tool, not only read as a teaching artifact. Faculty will examine the resulting session transcripts to understand how students work and to improve the tool. Publication is possible, which means an IRB protocol later; the design keeps that option open at low cost.
- 2026-10-12: one repository, two packages. No fork, no long-lived branch. Either would need every core fix (thinking-block replay, the Qwen3.8 default, the workdir boundary) ported by hand forever.
- 2026-10-12: transcripts reach faculty by student-initiated export and upload, not by automatic sync.
- 2026-10-12: the classroom backend is Rosie. Streaming is on by default in classroom mode (its own spec, `SPEC-streaming.md`, follows this one).

## Purpose

Core Filament exists to be read. CLAUDE.md says a reader should trace a task end to end in under five minutes, and it defers everything that would get in the way of that: streaming, retries, styling, telemetry. Those are the right rules for a reference harness and the wrong rules for a tool a student opens every day for a semester.

The classroom layer is the daily tool. It is built from the core's parts and plugs into the core's existing seams: the `ModelClient` Protocol, the `Tool` contract and `Registry`, the `TurnReporter` hook surface, and `Session`. It adds what a working tool needs and the core does not: a nicer interface, streaming, a record of which course and assignment a session belongs to, protection against secrets landing in transcripts, and a way to hand sessions in.

The transcript is the product. A semester of student sessions is the data faculty will use to see where the tool helps, where it fails, and what students actually ask it to do. So the transcript format stops being whatever `session.py` happens to write and becomes a contract with a version number.

## Scope

In v1:

- A second top-level package, `filament_classroom`, with its own entry point, `filament-class`. Core never imports it; a test enforces that.
- A dependency policy for the classroom package that differs from the core's.
- Assignment context from a `.filament-class.toml` file that faculty ship in each starter repo.
- A per-student pseudonymous participant id and a recorded research-consent flag.
- A transcript contract: a `session_start` event carrying a schema version, the backend and model, and classroom metadata.
- Secret redaction applied to every transcript line as it is written.
- `filament-class export`: bundle one assignment's sessions into a file the student uploads to Canvas.
- `filament-class info`: show the student what is collected and where it lives.
- Two small additive changes to core `session.py` and `cli.py`, listed under *Core changes*, each its own issue and PR.

Explicitly deferred to later specs:

- Streaming (`SPEC-streaming.md`, next).
- The interface itself. This spec fixes the dependency policy that decides what the interface can be built from; the interface is a separate brainstorm and spec.
- Confirmation prompts before `run_shell`, and any other safety rails beyond the workdir boundary.
- Automatic upload of transcripts anywhere.
- Analysis tooling on the faculty side. The schema is designed so a few lines of Python can read it; the tooling can come when there is data.
- A `session_end` event. Timestamps on the last event already give a session's end; a reason code would need the CLI to know why it is exiting, which it mostly does not.

## User-facing behavior

### Students

A starter repo for an assignment contains a `.filament-class.toml`:

```toml
course = "CSC-3310"
assignment = "hw3"
```

The student works inside it:

```
$ cd hw3 && filament-class
filament-class 0.1.0 — CSC-3310 hw3. /help for commands, /exit to quit.

> ...
```

Sessions are stored centrally, not under the project directory, so they do not scatter across repos and do not end up in a student's git history:

```
~/.local/share/filament-class/sessions/CSC-3310/hw3/20261012T140102000000.jsonl
```

When the assignment is due, the student exports and uploads:

```
$ filament-class export
CSC-3310 hw3: 4 sessions, 2026-10-09 to 2026-10-12
  20261009T190301000000.jsonl   31 events   6 tool calls
  20261010T203315000000.jsonl   58 events  14 tool calls
  20261012T140102000000.jsonl   12 events   2 tool calls   1 redaction
  20261012T151900000000.jsonl    9 events   1 tool call
participant: 3f9c1a2e
wrote CSC-3310-hw3-3f9c1a2e.zip
```

The student uploads the zip to the Canvas assignment. Canvas records who submitted it; the zip itself does not carry a name or email.

`filament-class info` prints the participant id, the consent status, the sessions directory, the active assignment if any, and a plain description of what a transcript contains (every prompt, every model reply, every tool call and its full result, including file contents the tool read). Students should be able to see this before they decide anything about consent.

### Faculty

Faculty ship `.filament-class.toml` in each starter repo and a classroom config with the Rosie endpoint:

```toml
# ~/.config/filament-class/config.toml
[rosie]
endpoint = "..."
model = "Qwen3.8-27B-FP8"
```

How that file reaches students (a setup script, a course page) is outside this spec. The endpoint is cluster-specific and does not appear in this repository.

Faculty receive zips from Canvas and unzip them into one directory. The `session_start` event in every transcript carries course, assignment, participant id, and consent status, so sorting and filtering needs no manifest beyond the files themselves.

### Consent

`filament-class consent` asks one yes/no question and records the answer. Until it is run, the recorded value is `null`, meaning "not asked", and nothing changes about how the tool works. The wording of the question, and whether a recorded flag is sufficient or a separate signed form is required, is for the IRB protocol to decide. The tool records a flag; it does not make the consent process. Faculty should expect to re-collect consent under whatever procedure the IRB approves. The flag exists so that if a protocol is approved mid-semester, sessions recorded before and after can be told apart, and so that sessions from students who declined can be excluded before anyone looks at them.

## Internal design

### Repository layout

```
filament/              # core, unchanged in character
filament_classroom/
├── cli.py             # filament-class entry point and subcommands
├── config.py          # config.toml, .filament-class.toml, identity
├── identity.py        # participant id, consent flag
├── redact.py          # secret patterns and the redactor
├── export.py          # bundle an assignment's sessions
└── ...                # interface and streaming land here later
tests/
├── classroom/         # offline tests for the classroom package
└── test_boundary.py   # core never imports filament_classroom
```

Both packages live in the one `pyproject.toml`. The classroom package's dependencies go in an extra:

```toml
[project.optional-dependencies]
classroom = [...]
dev = ["pytest", "mypy"]

[project.scripts]
filament = "filament.cli:main"
filament-class = "filament_classroom.cli:main"
```

`pip install filament` still gives the core with `httpx` alone. `pip install 'filament[classroom]'` gives students the tool.

### Dependency policy

Core keeps its rule: `httpx` and `pytest`, nothing else without explicit approval. The classroom package may take dependencies, each one named and justified in the spec that introduces it, approved by faculty before it lands. The justification is the same question CLAUDE.md asks, with a different answer expected: does this make the tool better for a student using it, and is it worth the install and maintenance cost? Interface libraries will be the first test of the policy and are decided in the interface spec, not here.

Two constraints hold regardless: a classroom dependency may not change what the core does (no monkeypatching core modules), and the classroom package must still run on Rosie login nodes over SSH, which rules out anything that needs a display.

### The import boundary

`filament` never imports `filament_classroom`. `tests/test_boundary.py` walks `filament/` and fails if any module imports from the classroom package. The classroom package imports freely from core, through public names only: `Conversation`, `run_interactive`'s building blocks, `build_client`, `build_registry`, `Session`, the types. If the classroom layer needs something core does not expose, that is a core issue, filed and reviewed under core's rules, not a reach into a private name.

### Assignment context (`filament_classroom/config.py`)

Resolution order, first match wins:

1. `--course` / `--assignment` flags.
2. `.filament-class.toml` in the working directory or any parent, nearest wins. Parsed with `tomllib` (standard library in 3.11).
3. Neither: the session is recorded with `course` and `assignment` set to `null` and stored under `sessions/unassigned/`. The tool still works; `export` refuses to bundle unassigned sessions without an explicit `--assignment`.

The classroom config file at `~/.config/filament-class/config.toml` (`$XDG_CONFIG_HOME` respected) supplies the Rosie endpoint and model. Environment variables `FILAMENT_ROSIE_ENDPOINT` and `FILAMENT_ROSIE_MODEL` override it, so the core's configuration still means what it meant. The classroom layer builds a core `Config` from these sources and hands it to core's `build_client`; it does not have its own client.

The backend defaults to `rosie` and the `--backend` flag is not offered. The Anthropic client is still reachable by setting `FILAMENT_BACKEND=anthropic` in the environment, because the core factory supports it and there is no reason to block it, but it is not documented for students. Whatever backend ran is recorded in `session_start`.

### Identity (`filament_classroom/identity.py`)

On first run, the tool generates a random UUID4, stores it in `~/.config/filament-class/identity.toml`, and uses it as the participant id from then on. The id is the only identifier in a transcript. It is stable across assignments, so a participant's sessions can be linked across a course, and it is meaningless on its own. The mapping from id to student exists only on the faculty side, through Canvas submissions. A student who wants a fresh id deletes the file; `info` says so.

`identity.toml` also holds the consent flag:

```toml
participant = "3f9c1a2e-...-..."
research_consent = true      # or false; absent means not asked
consent_recorded = "2026-10-12T14:01:02+00:00"
```

### Transcript contract

The transcript is JSONL, one event per line, as today. The contract adds one event at the start of every session and a version number.

**`session_start`** is the first line of every transcript, written by core `Session` (see *Core changes*):

```json
{
  "event": "session_start",
  "timestamp": "2026-10-12T14:01:02.000000+00:00",
  "schema_version": 1,
  "filament": "0.1.0",
  "backend": "rosie",
  "model": "Qwen3.8-27B-FP8",
  "mode": "interactive",
  "metadata": {
    "harness": "filament-class 0.1.0",
    "course": "CSC-3310",
    "assignment": "hw3",
    "participant": "3f9c1a2e-...-...",
    "research_consent": null
  }
}
```

`schema_version`, `filament`, `backend`, `model`, and `mode` (`oneshot` or `interactive`) are core's. `metadata` is opaque to core, in the same sense `provider_state` is: core writes whatever dict it is given and never reads it. The classroom layer owns the keys above. Core's own CLI passes `metadata={}`.

`model` matters because Rosie's model changes over time. Today a transcript records only `backend: "rosie"`, which will be ambiguous across semesters.

**Versioning.** `schema_version` is 1 for the format as it stands plus `session_start`. Adding a field or an event type does not bump it; readers must tolerate fields and events they do not know. Renaming or removing a field or event, or changing what one means, bumps it. The current version and a changelog live in `session.py`'s module docstring, so the number and the format it describes are in one place.

**Timing.** Every event already carries a UTC timestamp. Model-call latency is the gap between `model_call` and the following `model_response`; tool latency is the gap between `tool_call` and `tool_result`. Streaming will add time-to-first-token to `model_response` as an additive field. Nothing here requires a core change now.

### Redaction (`filament_classroom/redact.py`)

Transcripts hold whatever the tools read. A student who asks the agent to look at a `.env` file, or whose shell command prints an API key, gets that secret into a file they will upload to Canvas. Redaction is applied to every transcript line as it is serialized, so it catches secrets wherever they appear: prompts, tool arguments, tool results, and the replayed message lists in every later `model_call`.

The redactor is a pure function `str -> str` over the serialized JSON line, handed to core `Session` at construction (see *Core changes*). It replaces each match with `[redacted:<kind>]`, which contains no characters that need JSON escaping, so the line stays valid JSON.

Patterns in v1, each with its `kind`:

- `anthropic-key`: `sk-ant-` followed by key characters.
- `openai-key`: `sk-` followed by 20 or more key characters, when not already matched above.
- `aws-key`: `AKIA` followed by 16 uppercase alphanumerics.
- `github-token`: `ghp_`, `gho_`, `ghs_`, `ghr_`, or `github_pat_` prefixes.
- `private-key`: a `-----BEGIN ... PRIVATE KEY-----` block through its `END` line.
- `bearer`: `Bearer ` followed by a token on the same line.
- `env-secret`: a line of the form `NAME=value` where `NAME` contains `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, or `API_KEY`; the value is redacted, the name kept.

The patterns are a list of `(kind, compiled regex)` pairs so faculty can add one in one line. The redactor is tested on each pattern and on the JSON-validity property.

Two honest limits, stated in `info` and in the module docstring. First, redaction protects the transcript, not the model: by the time a line is redacted, the secret has already gone to the backend as part of the conversation. Keeping secrets away from the model is a different feature (a filter on tool results before they enter the conversation) and is not in this spec. Second, pattern matching misses secrets that do not look like secrets. `info` tells students to keep credentials out of the directories they run the tool in.

### Export (`filament_classroom/export.py`)

`filament-class export [--course C] [--assignment A] [-o FILE]` finds the assignment's sessions, prints the table shown above, and writes a zip named `<course>-<assignment>-<first 8 of participant>.zip` containing the session files and a `manifest.json`:

```json
{
  "schema_version": 1,
  "harness": "filament-class 0.1.0",
  "course": "CSC-3310",
  "assignment": "hw3",
  "participant": "3f9c1a2e-...-...",
  "research_consent": null,
  "exported": "2026-10-12T15:30:00+00:00",
  "sessions": [
    {"file": "20261009T190301000000.jsonl", "events": 31, "tool_calls": 6, "redactions": 0}
  ]
}
```

The per-session counts come from reading each file; the redaction count is the number of `[redacted:` occurrences. The table is printed before the zip is written so the student sees what is being bundled. There is no interactive confirmation in v1; a student who does not want to submit a session deletes it first (`info` prints the directory). `export` uses `zipfile` from the standard library.

Zip rather than a single concatenated JSONL because faculty will have many of them, and one file per session with the original timestamped name is easier to sort and diff than one blob per student.

### Classroom entry point (`filament_classroom/cli.py`)

Subcommands: none (run the tool), `export`, `info`, `consent`. The run path mirrors core's `cli.main` in shape: build config, build client, set the workdir root, build the registry, construct a `Session` with metadata and the redactor, then run. In v1 it calls core's `run_interactive`; the interface spec will replace that call with the classroom interface, and the streaming spec will change which client it builds. Those are the only two places later specs touch this module.

Errors are reported the way core reports them: `configuration error:` with exit 2 for a bad config, `error: <Type>: <message>` with exit 1 for a failed request.

## Core changes

Two additive changes to core, each filed as an issue and landed in its own PR under core's rules. Neither touches the agent loop, the `Tool` contract, or the `ModelClient` Protocol.

1. **`Session` writes `session_start` and accepts a redactor.** `Session.__init__` and `new_session` gain `backend`, `model`, `mode`, `metadata`, and `redact` parameters. The first line written is `session_start` as specified above. `_write` passes each serialized line through `redact` when one is given. `Config` gains a `model` property returning the model name for the configured backend, so the CLI has one place to read it. Core's `cli.py` passes the backend, model, mode, and an empty metadata dict. `schema_version` and its changelog go in the module docstring. Tests: `session_start` is the first event and carries every field; `redact` is applied to every line; `metadata` round-trips.

2. **The import-boundary test**, `tests/test_boundary.py`. Lands with the first classroom commit, since there is nothing to check before that.

Everything else is classroom-only.

## Constraints honored

- Core's constraints are untouched. The agent loop, `Tool`, `ModelClient`, the one-tool-per-module rule, and the no-per-backend-prompt rule all stand, and the classroom layer is bound by the last one too: same prompt, every backend.
- The core dependency rule is untouched. Classroom dependencies live in an extra and never reach a plain install.
- No classroom code is reachable from core, and a test proves it.
- The transcript stays append-only JSONL that `grep` and a few lines of Python can read. `session_start` and `metadata` are additive.
- Cluster details stay out of the repository. The endpoint lives in a config file faculty distribute.
- All classroom tests run offline against `tmp_path` and `io.StringIO`, with the existing `FakeClient`.

## Considered and rejected

- **A fork.** Two copies of the loop, the clients, and the specs, drifting from the first week. Every core fix ported twice.
- **A long-lived `classroom` branch.** The same drift with worse tooling; merges from `main` would conflict in exactly the files the class layer cares about.
- **Classroom features inside `filament/` behind flags.** Each flag is a branch in code a student is supposed to read in five minutes. The package boundary keeps the core honest about what it is.
- **Sessions under the project directory, as core does.** Core's default is right for a one-off run in a directory you own. For students it scatters transcripts across repos, risks committing them, and makes export a search problem.
- **Automatic upload.** A server, credentials, offline behavior, and a privacy question students did not sign up for. Student-initiated export keeps the student in control of what leaves the machine, which is also the right posture for consent.
- **Real names or emails in the transcript.** Canvas already links a submission to a student; putting the name in the file only makes the research dataset harder to de-identify later.
- **Redacting tool results before the model sees them.** Worth doing, but a different feature with different trade-offs (the model can no longer help with the file it was asked about). Transcript redaction is the FERPA-relevant one and is cheap.
- **A `ThinkingBlock`-style typed metadata schema in core.** Same reasoning as `provider_state`: core should not know the classroom's field names.
- **Making `schema_version` bump on every additive change.** It would bump monthly and readers would learn to ignore it.

## Testing strategy

All offline, under `tests/classroom/` unless noted.

- **Config.** `.filament-class.toml` found in the cwd and in a parent; nearest wins; flags override the file; no file yields `null` course and assignment and the `unassigned` directory. Environment variables override `config.toml`. A missing endpoint is a configuration error.
- **Identity.** First run creates the file with a UUID; second run reads the same id; `consent` writes the flag and the timestamp; absent flag reads as `None`.
- **Redaction.** One test per pattern; a line with no secrets is returned unchanged; the output of redacting any valid JSON line is valid JSON; a secret inside a nested replayed message list is caught.
- **Export.** Builds a zip with the expected name, the session files, and a manifest whose counts match the files; refuses unassigned sessions without `--assignment`; the printed table matches the manifest.
- **Entry point.** The run path constructs a core `Session` with the expected metadata and redactor (stub `run_interactive` the way `test_cli.py` stubs it); `info` output names the directory, the participant id, and the consent status.
- **Boundary.** `tests/test_boundary.py`: no module under `filament/` imports `filament_classroom`.
- **Core.** The `session_start` and redactor tests under core change 1 land in `tests/test_session.py` and `tests/test_cli.py`.

## Known risks

- **Rosie is a single point of failure.** Coursework depends on one model served from one Slurm job. Streaming (next spec) makes slow responses tolerable; it does not make an absent server tolerable. A permanent allocation is a prerequisite for using this in a graded course, not a nice-to-have.
- **Consent is not the tool's to grant.** The flag records an answer; whether that answer counts is for the IRB. Build the data path now, talk to the IRB before the first semester of data, and expect to re-collect.
- **Redaction has false negatives.** Pattern matching catches credentials that look like credentials. It does not catch a password that is a word. Students are told; it remains a risk.
- **Transcripts are large.** Each `model_call` event carries the full message list, so a long session's transcript grows quadratically. A semester of students multiplies that. Fine for grep; worth knowing before anyone loads a term's data into memory at once.
- **Participant ids are per machine.** A student using two laptops gets two ids. Linking them is a faculty-side step through Canvas; the tool does not try.

## Future work

When and if real needs arise, not before:

- `SPEC-streaming.md`: streaming clients that return a complete `Response`, on by default here.
- The interface spec, after the dependency decision this spec sets up.
- A confirmation prompt before `run_shell`, and a per-assignment allowlist of commands.
- A `session_end` event with a reason code once the CLI has a clean place to know it.
- Faculty-side analysis helpers: load a directory of zips into one table, per-participant summaries.
- Filtering secrets out of tool results before they reach the model.
- Automatic upload, if student-initiated export proves too lossy.
