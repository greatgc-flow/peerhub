# Phase 3 dispatch-loop shared contract surface (2026-08-12)

Status: **first draft, for dialectical ratification.** Same process as the
capability-lease design and the broadcast design: this document is the
artifact a ratification round votes on, not a plan already agreed.

Scope: Phase 3's 5 vague roadmap bullets (adapter-wiring loop, session
continuation, streaming decode, error-taxonomy mapping, tool-call
parsing) — bundled as one design topic per the ratified execution plan,
because they share invariants on the same `PeerAdapter`/`AskResult`/
`DecodedOutput` surface. This document ratifies only the **shared
contract surface**; each facet's own implementation is a separate,
independently-verified L2 increment once this lands.

Synthesized from two split empirical investigations (ag.deepthink:
session continuation + tool-call parsing; cx.deepthink: base loop +
streaming + error taxonomy — both read the actual source and, for
streaming, a live Codex CLI probe, not assumption).

---

## 1. The roadmap's framing was stale — corrected premise

All five bullets read as "not started." Investigation found this is only
true for the *last mile* of each:

- **Adapter wiring**: single-attempt dispatch (resolve adapter → admit →
  plan → spawn/supervise → decode → assess → commit) already works
  end-to-end for all 3 real adapters (`direct_ask.py:139`,
  `workflows.py:540`). What's actually missing is the **outer bounded
  retry/resume loop** — `authorize_retry()` already exists
  (`workflows.py:486`, with replay-safety and reconciliation rules) but
  nothing calls it after a failed attempt.
- **Session continuation**: `AdapterRequest.requested_session_action` and
  `SessionHint.external_session_id` already exist in the contract.
  `direct_ask.py` just always passes `SessionAction.NONE`. The 3 real
  CLIs use 3 different resume mechanisms (verified):
  `claude.cmd --resume <id>`, `codex.cmd`'s `exec resume <id> ...`,
  `agy.exe --conversation <id>`.
- **Streaming**: `OutputDecoder.feed()`/`finalize()` already has the
  right incremental shape (`adapters/contract.py:583`). Every real
  decoder currently defeats it — `feed()` just buffers bytes, and the
  decoder isn't even constructed until after the process exits
  (`workflows.py:857`). A live probe confirmed Codex's actual JSONL
  event shape (`thread.started`, `turn.started`, `item.completed`,
  `turn.failed`) is real and stable enough to parse incrementally.
- **Error taxonomy**: peerhub already has `ErrorCode`,
  `OperationalFailureCategory`, and `ErrorDetail` (`core/protocol.py`) —
  a taxonomy at least as rich as hub.py's. The loss happens at the
  adapter/result boundary: all 3 real adapters flatten every nonzero
  exit, malformed output, and empty response to `ErrorCode.INTERNAL_ERROR`,
  and `AskResult` carries no `ErrorDetail`/category at all.
- **Tool-call parsing**: genuinely greenfield. Zero test fixtures exist
  for any of the 3 CLIs' tool-call JSON shape, and today's adapters
  silently discard tool-call data if a peer emits it (`claude_adapter.py`
  only extracts `result`; `codex_adapter.py` only extracts
  `agent_message`-typed items).

## 2. Ratified-shape additions (the actual proposal)

All additive — no existing field/type is removed or restructured.

**`DecodedOutput`**: add `session_id: str | None = None`. (A
`DecoderEventKind.SESSION_IDENTITY` event already exists but is a stream
marker; elevating the final session ID to a concrete field lets core
capture it without re-parsing the event stream.)

**`DecoderEventKind`**: add `TOOL_CALL`. `DecoderEvent` already accepts a
loosely-typed `payload: Mapping[str, JsonValue]`, so a tool call's
`{"name": ..., "arguments": ...}` shape embeds directly — no new payload
type needed.

**`AskResult`**: add
```python
error_detail: ErrorDetail | None = None
operational_failure_category: OperationalFailureCategory | None = None
```

**`ErrorCode`**: add only the 3 distinctions that cannot currently be
expressed: `PROCESS_EXIT_NONZERO`, `OUTPUT_LIMIT_EXCEEDED`,
`SESSION_INVALID`. Everything else (spawn failure, timeouts, lease/
routing/protocol-assessment failures) already has a code.

**`ProtocolAssessment`**: unchanged. Stays protocol-only (its own
documentation says so) — auth/quota/network/retry-policy do not belong
there.

**Streaming**: no `OutputDecoder` or `PeerAdapter` signature change.
Add a channel-tagged, sequence-tagged process-chunk DTO; serialize
stdout/stderr delivery through one consumer (never feed a decoder
concurrently from two reader threads); construct the decoder *before*
`run_process()` instead of after exit; add an optional non-blocking
decoder-event sink at the workflow boundary so events can reach callers
pre-terminal. `Capability.STREAM` is only advertised once an adapter
actually emits pre-terminal events — Codex is the near-term candidate
(JSONL); Agy/Claude currently request terminal-only JSON output formats,
so they stay non-streaming until that changes independently.

**Retry/resume loop**: a new outer method, layered *above*
`dispatch_and_execute()` (which stays exactly one attempt — no
duplication of lease/capability/admission/completion logic inside it).
The outer loop consumes the previous `AskResult`, calls the existing
`authorize_retry()`, and decides the next attempt. **`RetryDisposition`
is computed centrally by this loop from execution certainty + replay
safety + reconciliation + session validity + routing state — an
adapter's own error classification must never itself authorize a
retry.** This is the one hard invariant in this design; every mapping
in §3 respects it.

## 3. Error-code mapping (grounded in real observed failure modes)

Measured from `.ai/ask_history.jsonl` (not speculative): historically
dominated by `terminal_timeout` (70), `nonzero_exit` (70), `timeout` (33),
`rate_or_session_limit` (24), `query_file_missing` (16),
`lease_expired` (7), `auth_error` (6), `empty_response` (5) — several of
these hit *this session*, directly (session resume failures, ask-id
collisions, a genuine silent hang traced to a stale credits cache).

| Real failure | Maps to |
|---|---|
| CLI executable missing | `SPAWN_FAILED` + `EXECUTABLE_UNAVAILABLE` |
| Sandbox/environment failure | `SPAWN_FAILED` + `ENVIRONMENT_UNAVAILABLE` |
| Deadline / silent stall | existing `PROCESS_TIMEOUT` / `SILENCE_TIMEOUT` |
| Nonzero exit | new `PROCESS_EXIT_NONZERO`, category only when evidence supports one |
| Invalid resumed session | new `SESSION_INVALID` — enables a deliberate fresh-session fallback |
| Auth / network / provider / rate / quota | existing `OperationalFailureCategory` values |
| Malformed / truncated / empty output | existing `PROTOCOL_ASSESSMENT_FAILED`, with `ProtocolAssessment`'s existing booleans carrying the precise reason |
| Lease / idempotency collision / missing query artifact / governance guard | stays a coordinator/admission failure, not vendor taxonomy |

One deliberate correction from hub.py's own taxonomy: hub.py's PTY layer
internally distinguishes a hard `deadline` from a silent-stall `zombie`,
but both collapse into `terminal_timeout` in its durable history — this
design keeps them distinct (`PROCESS_TIMEOUT` vs `SILENCE_TIMEOUT`
already exist separately in `dispatch/process.py`) rather than
reproducing hub.py's loss of information.

## 4. What's explicitly NOT in this design

- **Full retry/resume loop implementation.** This document ratifies the
  contract surface and the one hard invariant (§2's retry-authorization
  rule); the loop itself is a separate L2 increment.
- **Per-adapter streaming implementation** (only Codex's decoder
  actually needs changing to exploit the new sink; Agy/Claude stay as
  they are until/unless they gain a streaming-capable output format).
- **Tool-call *semantics*** (what peerhub does with a captured tool
  call) — only the capture mechanism (`TOOL_CALL` event) is ratified
  here.
- Real end-to-end test coverage for Claude and Codex's real-adapter path
  (only Agy currently has a slow real-CLI integration test) — a gap
  worth its own follow-up, not blocking this contract surface.

## 5. Open questions for the ratification round

1. Is the retry-authorization invariant (§2, "an adapter's own error
   classification must never itself authorize a retry") correctly
   load-bearing, or does it need a documented exception?
2. Is 3 new `ErrorCode` values genuinely sufficient, or does the
   ratification round find a real observed failure mode (check
   `.ai/ask_history.jsonl` yourself) that doesn't map cleanly onto §3's
   table?
3. Does deferring per-adapter streaming to Codex-only (§4) leave a real
   gap, or is it correctly scoped to "streaming only where an adapter can
   actually produce pre-terminal events today"?
