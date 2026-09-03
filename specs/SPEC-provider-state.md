# Filament — Provider State Replay

A design spec for carrying backend-private state (today: Anthropic thinking blocks) across turns without letting it leak into the agent loop. This is a component spec; load-bearing constraints in `CLAUDE.md` apply.

**Status: proposed, not implemented.** Written 2026-09-02 to record a design discussion and the decision that came out of it. Implement it when the Anthropic default is to move to a Claude 5 model.

## Decision record

- 2026-09-02: keep `claude-sonnet-4-6` as the Anthropic default. Every Claude 5 model runs thinking by default and requires its thinking blocks to be replayed, and the client has nowhere to keep them. Switching the default string alone would fail on the second turn of any tool-use conversation. Implement this spec first; then the default flips to `claude-sonnet-5` in one line and the lower price comes with it.

## Purpose

Filament's internal types are backend-agnostic on purpose: the loop sees `Message`, `ToolCall`, and `Response`, and each model client absorbs its wire format. That held while every backend's reply could be reduced to text plus tool calls.

Current Anthropic models break that assumption. When thinking is on, a reply carries thinking blocks that the harness must send back unchanged on the next request. They are signed, and the API rejects a continuation that drops or edits them. Where thinking stands when a request omits the `thinking` parameter, per Anthropic's documentation as of this writing:

| Model | Omitting `thinking` means | Can be disabled? |
|---|---|---|
| Sonnet 4.6, Opus 4.6 | off | it is the default |
| Opus 4.7, Opus 4.8 | off | yes |
| Sonnet 5 | on, adaptive | yes |
| Opus 5 | on, adaptive | only at effort `high` or below |
| Fable 5, Fable 5.1 | on, always | no |

So Sonnet 4.6 is the last Sonnet where sending nothing means no thinking, and the top tier cannot be told not to think. A harness that wants to run any Claude 5 model with its default behavior has to carry the blocks. This is not going away, and it is not Anthropic-specific in kind: OpenAI's newer API hands back reasoning items with the same replay requirement.

Rosie is a separate story. The default model there, Qwen3-Coder-30B-A3B-Instruct, does not think. Thinking Qwen variants served by vLLM return reasoning in a separate `reasoning_content` field when a reasoning parser is configured, and the history sent back does not include it. Nothing to replay on that side.

The teaching point is real: modern backends give the harness state it must echo without understanding, and that is why real harnesses are careful about editing history. A student who sees the field learns that.

## Scope

In v1:

- One opaque field, `provider_state`, on `Response` and `Message`. The loop copies it from the response onto the assistant message it already builds. Nothing outside the producing client reads it.
- The Anthropic client fills it from thinking blocks and puts them back, first, in the assistant turn on replay.
- The Rosie client ignores it in both directions.
- The session logs it as part of the message and response records it already writes.
- The Anthropic `max_tokens` cap rises, because thinking tokens count against it.

Explicitly deferred:

- Sending a `thinking` parameter at all. v1 sends none, so each model's default applies. Differences are data.
- Readable reasoning in transcripts (`display: "summarized"`). Needs an explicit `thinking` parameter, which would turn thinking on for 4.6-era models, so it is a configuration knob rather than a default. See *Future work*.
- Recording Rosie's `reasoning_content` in the transcript.
- A `refusal` stop reason surfaced like `truncated`.

## User-facing behavior

None, by design. A student running Sonnet 5 sees the same `[thinking...]`, interstitial text, `[tool]` lines, and final answer as today. The difference is that the second turn of a tool-use conversation works instead of returning a 400.

In the transcript, each `model_response` event and each assistant message in later `model_call` events carries a `provider_state` value. On current models the thinking text inside those blocks is empty by default; the block is present and signed, and that is what gets replayed. Workshop materials should say so, because an empty thinking block looks like a bug to someone who has not been told.

## Internal design

### Internal types (in `filament/types.py`)

```python
@dataclass
class Response:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    provider_state: object | None = None

@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    provider_state: object | None = None
```

`provider_state` is opaque: JSON-serializable data that only the client which produced it may interpret. The name says whose it is and that it is state, not content. It is typed `object` rather than a `ThinkingBlock` dataclass on purpose. A typed shape would put Anthropic's wire format into `types.py`, which is exactly what the internal types exist to keep out.

### Loop change (in `filament/agent.py`)

One rule, no special cases: whatever the client handed over, hand it back. Wherever `send()` appends an assistant message built from a `Response` (the final-answer path and the tool-call path), it copies `response.provider_state` onto that message. The three sentinel paths (empty output, truncation, iteration cap) append synthetic assistant messages and leave the field `None`; the model's output on those turns is not replayed today either.

The loop never reads the field. It stays append-only, which is the other rule current models enforce: Fable 5.1 rejects a request whose earlier turns were edited after thinking blocks were produced. `/reset` is fine, since it starts a new conversation rather than editing one.

### Anthropic client (in `filament/model_clients/anthropic.py`)

- **Parsing.** `_from_wire_response` collects every `thinking` and `redacted_thinking` block verbatim, in order, into `provider_state` (for example `{"thinking_blocks": [...]}`). Text and tool_use handling is unchanged.
- **Replay.** `_to_wire_messages` emits an assistant message's thinking blocks first, then its text block, then its tool_use blocks, matching the order the API returned them. Blocks go back byte-for-byte, including ones whose thinking text is empty.
- **`max_tokens`.** Thinking tokens count against the cap. 4096 is too small once thinking is on; 16384 is the working figure. That interacts with the 120-second request timeout, and Sonnet 5's tokenizer produces roughly 30% more tokens than 4.6's for the same text. A truncation mid-thinking already surfaces through `Response.truncated` (issue 17), so the failure is visible either way.
- **Request.** No `thinking` parameter in v1.

### Rosie client

Ignores `provider_state` when translating in either direction. A comment says why.

### Session

`dataclasses.asdict` already serializes every field, so the transcript picks up `provider_state` with no code change. Each `model_call` event logs the full message list, so thinking blocks are repeated in every later event of the same conversation; transcript size grows accordingly.

## Constraints honored

- `Tool` contract and `ModelClient` Protocol signature unchanged.
- No backend-specific logic in the loop: it copies one field it never reads.
- Wire-format details stay inside the Anthropic client. The internal types gain an opaque slot, not a shape.
- No per-backend prompt tuning. No `thinking` parameter is sent; the models' own defaults apply.
- No new dependencies. All unit tests offline; one integration test proves replay against a live model.

## Considered and rejected

- **A stateful client with a side table** keyed by tool-call id, re-inserting blocks on replay. Keeps the types pure, but hides state in an object that is otherwise pure configuration, and breaks quietly across `/reset` or two conversations sharing a client. Hidden state is the wrong lesson for a teaching harness.
- **Storing thinking as visible content text.** The blocks are signed; reconstructed blocks are rejected.
- **A typed `ThinkingBlock` internal type.** Leaks one vendor's shape into `types.py`. Opaque is the point.
- **Disabling thinking in the client** so the problem never arises. Works on Sonnet 5, works on Opus 5 only at effort `high` or below, impossible on Fable, and it switches off the feature that makes these models better at agentic work. Acceptable as a stopgap, not chosen.
- **Switching the default model first and adding replay later.** The loop would 400 on the second turn of every tool-use conversation. Replay first, then the default.

## Testing strategy

All offline except the last item.

- `tests/test_model_clients.py`: a Messages-API reply with a thinking block, a text block, and a tool_use block parses to a `Response` whose `provider_state` holds the thinking block verbatim and whose text and tool calls are unchanged. On replay, an assistant `Message` with `provider_state` renders its thinking blocks first, then text, then tool_use. A reply with no thinking blocks leaves the field `None`.
- `tests/test_agent.py`: the assistant message appended for a tool-call turn and for a final turn carries the response's `provider_state`; sentinel turns carry `None`; the next `model_call` sees it.
- `tests/test_session.py`: `provider_state` appears in the `model_response` event and in the assistant messages of later `model_call` events.
- `tests/test_model_clients.py`, marked `@pytest.mark.integration`: a two-step tool-use conversation against `claude-sonnet-5`, proving the second request is accepted. This is the contract that matters.

## Known risks

- **Model-bound blocks.** Fable-tier thinking blocks are readable only by Fable-tier models; other models drop them. Filament fixes the model per process, so this does not bite today, but a transcript replayed against a different model would not carry its reasoning.
- **Empty thinking text.** Students will see signed blocks with empty text in transcripts and wonder whether anything happened. Say so in workshop materials, or turn on summaries (see *Future work*).
- **Transcript growth.** Thinking blocks in every subsequent `model_call` event compound the existing quadratic growth.
- **Refusals.** Claude 5 models decline some requests with `stop_reason: "refusal"`. Today that surfaces as the empty-output sentinel, which is honest but uninformative.

## Future work

When and if real needs arise, not before:

- Flip the Anthropic default to `claude-sonnet-5` once this lands. One line in `config.py`.
- A `FILAMENT_ANTHROPIC_THINKING` setting (`adaptive`, `disabled`, or unset) and a display option so transcripts carry readable reasoning summaries.
- A `refusal` flag on `Response`, surfaced like `truncated`.
- Record Rosie's `reasoning_content` in the transcript when a reasoning parser is configured. Data, not replay.
