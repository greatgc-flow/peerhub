# Stage 3 Scoping: First Real Adapter (2026-08-08)

**Status: scoped, corrected, ready for implementation.**

## Target: `agy.exe` (Antigravity CLI)

ag.deepthink proposed this after directly invoking all three candidate CLIs (`claude.cmd`, `codex.cmd`, `agy.exe`). **One of its three comparative claims was independently re-verified by cc and found false; corrected below.** The target choice itself still holds on agy.exe's own merits.

### Corrected evidence

| CLI | ag's claim | cc's independent re-check | Verdict |
|---|---|---|---|
| `claude.cmd -p "say hello"` | Output "polluted" by hub.py markers (`[HUB:WARN]`, room-status HTML) | Ran directly: clean single-line response, zero hub.py noise | **ag's claim was false** — claude.cmd's raw output is clean when invoked outside hub.py. Not disqualifying; a real Claude adapter remains equally viable for a future slice. |
| `codex.cmd exec "say hello"` | "Stalled indefinitely... requires complex stdin handling or PTY wiring" | Ran with `stdin=</dev/null`: completed cleanly, exit 0, structured session output (model, provider, sandbox, token count) | **Overstated.** It waits for stdin EOF by design (lets you pipe extra context) — trivially avoided with `stdin=subprocess.DEVNULL` in a real Python subprocess call, not "complex handling." Not disqualifying either. |
| `agy.exe -p "say hello" --output-format json` | Has native `--output-format text\|json\|stream-json`, clean synchronous single-shot response, no stdin block | Confirmed via `agy.exe --help`: the flag is real | **Confirmed accurate.** |

**Conclusion:** agy.exe remains the reasonable first target (real structured JSON output mode, no stdin gotcha, no PTY needed for `-p` mode) — but on its own genuine merits, not because the alternatives are broken. claude.cmd and codex.cmd are both legitimate targets for a later slice; nothing here rules them out.

## Protocol mapping (`peerhub/adapters/contract.py`'s `PeerAdapter`)

- **`plan_invocation`**: `argv=("agy.exe", "-p", request.prompt_content, "--output-format", "json")`, `transport=TransportKind.PIPE`, `stdin_payload=None`, `environment_delta={}` (ambient auth for now).
- **`new_decoder`**: buffers raw chunks, parses the full buffer as JSON on `finalize()`, emits `DecoderEvent(kind=ASSISTANT_TEXT, ...)` from the parsed response field (exact JSON schema to be confirmed empirically during implementation — run `agy.exe -p "..." --output-format json` for real and read the actual keys, don't assume a shape).
- **`interpret_output`**: checks `exit_code == 0` and successful JSON parse; returns `ProtocolAssessment(parsed=True, response_present=True, vendor_completion_marker=None, suspected_truncation=False, protocol_failure=None)` on success, an appropriate failure otherwise.

## Smallest honest "done" for this slice

1. `RealAgyAdapter` implementing `PeerAdapter`, one `ProfileDescriptor` (`ag.standard`).
2. Translates a basic `AdapterRequest` into the `agy.exe -p ... --output-format json` `InvocationPlan`.
3. A decoder that extracts final text from the real observed JSON shape.
4. **Proof**: an integration test that actually shells out to real `agy.exe` (not mocked) at least once, asserts a successful `ProtocolAssessment`.

## Explicitly deferred (not in this slice)

- Session continuation (`--conversation`/`--continue` flags exist but state-persistence wiring is Stage 4/5).
- Streaming (`--output-format stream-json`) — buffer the full response for now.
- Error-taxonomy mapping — any non-zero exit or parse failure is a generic `protocol_failure`.
- PTY transport, tool-call parsing.
- A second/third real adapter (claude.cmd, codex.cmd) — legitimate future slices, not started here.
