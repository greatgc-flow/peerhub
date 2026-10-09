# Explicit Per-Thread Native Resume (cc, cx, ag) - Design

Status: implemented (all three stages, 2026-10-10; design approved by ag/cx cross-critique, 2026-10-09). Motivation and measurements: `CONTEXT_EFFICIENCY.md`.
Principles: Records stay the authority; a fresh session with bounded catch-up stays the default and the fallback; resume is an explicit per-ask opt-in; ag is supported but measured no token saving (`CONTEXT_EFFICIENCY.md`); it is an explicit opt-in for continuity only

## Surface
- `peerhub ask <cc|cx|ag> ... --resume` (default off). Omitting it always starts a fresh generation. Repeat the flag on each continuing ask; there is no sticky setting.
- `ask --json` reports the effective mode (`resumed` or `fresh`), the fallback reason if any, and the injected Record ids.

## Vendor session identity
- cc: `session_id` of the final successful `result` event; resumed with `--resume <id>`. cx: `thread_id` of the `thread.started` event; resumed with `codex exec resume <id>` (resume inherits the sandbox, so `-s` is not passed; the writable mode is part of the binding).
- ag: `conversation_id` of the flat JSON response; resumed with `agy --conversation <id>` (print mode keeps `-p <prompt> --output-format json` and model/effort flags).
- IDs are bounded opaque strings (1-200 characters, `A-Za-z0-9._:-`); conflicting IDs in one run are rejected. Never use a vendor "latest session" selector.
- The adapter returns the candidate vendor id inside the successful terminal payload (no extra runtime event). The Bridge persists it in a new nullable `bridge_sessions.vendor_session_id` inside the terminal transaction, under the claim fence (current owner, generation, unexpired lease, expected old value). The local `external_session_id` stays the process/cancel handle.
- The mapping is scoped to (stream, peer) and bound to: canonical provider cwd, resolved model/effort/profile, writable mode, adapter fingerprint (versioned), and the workspace generation. Any change means a new generation.

## Catch-up for a resumed session
- A per-generation watermark records the highest Record position that the native session has been given (or generated itself); it is committed with the terminal result of each delivery.
- A resumed session receives every Record after the watermark and before the current one, in order, each with the same `[position id author kind]` provenance wrapper, plus control redirects. The current Record is not repeated. The session's own materialized responses count as already represented.
- Resume is only eligible if the generation's bootstrap was not truncated. If the unseen delta exceeds the inline limit, fall back to a fresh generation; a native delta is never silently truncated.
- "Exactly once" means once per Record within a committed native continuity; vendor acceptance across a crash cannot be proven, so uncertainty blocks instead of replaying.

## Fallback and uncertainty
- Missing id, binding or workspace change, pre-run rejection of the resume, oversized delta, or explicit omission of `--resume` -> new generation with bounded catch-up and the existing deterministic `context.boundary` Record carrying the reason.
- A spawned CLI failing after start is not proof that no turn ran: the delivery stays uncertain (MAY_HAVE_STARTED is never auto-replayed) and the generation is marked LOST so the next authorized attempt starts fresh.
- `control.cancel` that may have affected execution marks the generation LOST regardless of the kill outcome. Recovery marks sessions LOST for unresolved invocation markers and started deliveries without terminal truth. A stale owner may append evidence but never mutates the mapping.

## Provenance and risks
- Evidence records: vendor id digest, binding and fingerprint, claim and session generations, execution/delivery ids, injected Record ids and omissions, resume outcome, boundary reason, cancellation, usage (cx usage is cumulative per thread: store the baseline and report deltas; ag counters may be cumulative per conversation, subject to measurement).
- Risks accepted and documented: hidden vendor state not in Records, stale remembered file facts (resumed peers are told to re-read files), session drift, secrets retained in vendor transcripts (PeerHub never copies raw transcripts into Records).

## Stages
1. Adapters (`adapters/base.py`): `ProviderSpec.argv` with an optional vendor id, id parsing for cc/cx/ag, terminal payload `vendor_session`, binding additions, explicit resume opt-in for all three peers.
2. Bridge (`bridge.py`): column migration, `_resolve_session`, watermark commit, `_needs_catch_up`/`_catch_up_for`, `_finalize`, LOST rules, cancel/recovery tainting.
3. Surface: `ask.py`, `cli/app.py` (`--resume`), command inventory, docs, tests (`test_native_resume.py` plus restart, takeover, crash-window, opt-out, binding-change, overflow and no-replay cases).
