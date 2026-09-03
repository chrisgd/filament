# Filament

Filament is a minimal agent harness for the Diercks School of Advanced 
Computing (@ Milwaukee School of Engineering) primarily for teaching/learning 
about agents. This Python-based CLI runs a read/decide/act/observe loop 
against either Rosie, the MSOE supercomputer, via an open-weights model 
served by vLLM with an OpenAI-compatible API, or the Anthropic Messages API.

## Installation
To install and run this, do the following:
```
git clone <repo>
cd filament
pip install -e .            # to use it
pip install -e ".[dev]"     # to develop / run tests
```

## Usage
Filament has two modes, one that runs against Anthropic, one that runs against Rosie.

To run against Rosie (the default backend), set the endpoint and model:
```
FILAMENT_ROSIE_ENDPOINT=http://localhost:8000/v1 \
FILAMENT_ROSIE_MODEL=some-open-model \
filament "read the README and tell me what this project does"
```

To run against Anthropic:
```
FILAMENT_ANTHROPIC_API_KEY=sk-ant-... \
filament --backend anthropic "read the README and tell me what this project does"
```

Omit the task argument to start interactive mode — a multi-turn conversation
in which the agent remembers prior turns. Type `/help` at the prompt for the
list of slash commands.
```
filament
```

Session transcripts are written to `./filament-sessions/{timestamp}.jsonl`.

## Tests

```
pytest                 # offline tests
pytest -m integration  # contract tests that hit a real backend
```

## Further reading

See [`CLAUDE.md`](CLAUDE.md) for architecture, design principles, and the
procedures for adding tools and backends.
