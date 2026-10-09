# Long-Horizon Continuity

Key distinction:

```text
Stream ≠ Session ≠ Context ≠ Process ≠ Harness
```

## Progress Questions
Check Record → operational event → observation → runtime event in that order,
and spend quota on direct AI questions only when that evidence is insufficient.

## Session Loss
fresh session + Stream catch-up.

## Context limit/compaction
Treat as a normal lifecycle event. Use a bounded catch-up projection rather than reinjecting the entire transcript.

## Pause
Persist pause Record → best-effort interrupt.
`PAUSED != ROLLED_BACK`.

## Resume
Prefer a compatible session; use a fresh generation on failure.

## Redirect
Use the same Stream for the same goal.

## New direction
Use a new Stream when the goal itself changes. Keep the existing Stream as history/reference.

## Quota exhaustion
Core state remains unchanged. Update only Observation to unavailable/exhausted.
Automatic Peer switching belongs to a future Routing/Orchestration extension.

## M1 Spec Alignment (2026-10-09)
CLI adapters support native terminate for active local deliveries: control.cancel kills the process tree and waits for observed exit. Resume, interrupt, and steer remain unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Cancelled deliveries remain uncertain and cannot auto-replay; a missing local process raises RuntimeTargetError("nothing running"), recorded as failed when durable delivery evidence still identifies a target. Bridge targets already known to be absent or stale retain nothing_running/stale_target outcomes. Durable fresh-session catch-up provides continuity but does not itself stop a provider invocation.
