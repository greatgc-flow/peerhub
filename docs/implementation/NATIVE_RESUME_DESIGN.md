# Current-slot Native Resume (cc, cx, ag)

Status: implemented (all three stages, 2026-10-10; design approved by ag/cx cross-critique, 2026-10-09). Motivation and measurements: `CONTEXT_EFFICIENCY.md`.
PR1 update, 2026-10-14 (uncommitted, deterministic validation pending): caller preference has been removed; ordinary asks reuse the compatible current slot. Records stay authoritative, and fresh sessions receive bounded catch-up. Historical measurements in `CONTEXT_EFFICIENCY.md` found no ag token saving; this change makes no new savings claim.

## Surface
- `peerhub ask <cc|cx|ag> ...` reuses the compatible current peer/Stream session. Use a dedicated Stream for a separate task. Retained-session selection and inspection remain future work.
- `ask --json` reports the effective mode (`resumed` or `fresh`), the fallback reason if any, and the injected Record ids.

## Vendor session identity
- cc: `session_id` of the final successful `result` event; resumed with `--resume <id>`. cx: `thread_id` of the `thread.started` event; resumed with `codex exec resume <id>` (resume inherits the sandbox, so `-s` is not passed; the writable mode is part of the binding).
- ag: `conversation_id` of the flat JSON response; resumed with `agy --conversation <id>` (print mode keeps `-p <prompt> --output-format json` and model/effort flags).
- IDs are bounded opaque strings (1-200 characters, `A-Za-z0-9._:-`); conflicting IDs in one run are rejected. Never use a vendor "latest session" selector.
- The adapter returns the candidate vendor id inside the successful terminal payload (no extra runtime event). The Bridge persists it in a new nullable `bridge_sessions.vendor_session_id` inside the terminal transaction, under the claim fence (current owner, generation, unexpired lease, expected old value). The local `external_session_id` stays the process/cancel handle.
- The mapping is scoped to (stream, peer) and bound to: canonical provider cwd, resolved model/effort/profile, writable mode, adapter fingerprint (versioned), and the workspace generation. Any change means a new generation.

## Catch-up for a resumed session
- A per-generation watermark records the last consumed prompt position, committed with the terminal result. Appending a response never advances it: prompt 5 / concurrent record 6 / response 7 leaves watermark 5, so the next delta includes record 6.
- A resumed session receives every Record after the watermark and before the current one, in order, each with the same `[position id author kind]` provenance wrapper, plus control redirects. The current Record is not repeated. Later same-peer responses are included conservatively; the previous response may be injected again. Redirects addressed only to other peers are excluded.
- Resume is only eligible if the generation's bootstrap was not truncated. If the unseen delta exceeds the inline limit, fall back to a fresh generation; a native delta is never silently truncated.
- Vendor acceptance across a crash cannot be proven, so uncertainty blocks instead of replaying. Old terminal receipts may lack complete context proof because their deltas excluded same-peer responses and could skip concurrent records. The writer upgrade reconstructs the consumed prompt only from an exact, complete terminal/context chain; otherwise it marks the native mapping LOST and clears vendor identity, watermark and usage baseline; it fixes known CLI capability values and preserves custom local-session capability/resolution. The versioned consumed-prompt rule applies permanently; reopen is idempotent and immutable Records/delivery truth are retained.

## Fallback and uncertainty
- Missing id, binding or workspace change, pre-run rejection of the resume, oversized delta -> new generation with bounded catch-up and the existing deterministic `context.boundary` Record carrying the reason.
- A spawned CLI failing after start is not proof that no turn ran: the delivery stays uncertain (MAY_HAVE_STARTED is never auto-replayed) and the generation is marked LOST so the next authorized attempt starts fresh.
- `control.cancel` that may have affected execution marks the generation LOST regardless of the kill outcome. Recovery marks sessions LOST for unresolved invocation markers and started deliveries without terminal truth. A stale owner may append evidence but never mutates the mapping.

## Provenance and risks
- Evidence records: vendor id digest, binding and fingerprint, claim and session generations, execution/delivery ids, injected Record ids and omissions, resume outcome, boundary reason, cancellation, usage (cx usage is cumulative per thread: store the baseline and report deltas; ag counters may be cumulative per conversation, subject to measurement).
- Risks accepted and documented: hidden vendor state not in Records, stale remembered file facts (resumed peers are told to re-read files), session drift, secrets retained in vendor transcripts (PeerHub never copies raw transcripts into Records).

## Stages
1. Adapters (`adapters/base.py`): `ProviderSpec.argv` with an optional vendor id, id parsing for cc/cx/ag, terminal payload `vendor_session`, binding additions, static resume capability for all three peers.
2. Bridge (`bridge.py`): column migration, `_resolve_session`, watermark commit, `_needs_catch_up`/`_catch_up_for`, `_finalize`, LOST rules, cancel/recovery tainting.
3. Surface: `ask.py`, `cli/app.py` (default current-slot reuse), command inventory, docs, tests (`test_native_resume.py` plus restart, takeover, crash-window, upgrade, concurrent-watermark, binding-change, overflow and no-replay cases).
