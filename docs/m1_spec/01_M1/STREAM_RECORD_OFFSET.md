# Stream / Record / Offset

## Ordering
Do not calculate `MAX(seq)+1` in the application as the existing Room does.
Use the monotonically increasing append position assigned by the SQLite transaction as the SSOT.

## Idempotency

```text
same idempotency key + same canonical payload -> existing Record
same idempotency key + different payload      -> conflict
```

## Delivery semantics

```text
Record append = idempotent
Record order  = deterministic
runtime delivery = at-least-once / evidence-based
external side effect = may be uncertain
```

Do not confuse exactly-once DB append with exactly-once AI execution.

## Control Records

```text
control.pause
control.resume
control.redirect
control.cancel
context.boundary
```

Control represents durable user intent; Session Bridge is responsible for the actual interrupt/steer.

## M1 Spec Alignment (2026-10-09)
CLI adapters support native terminate for active local deliveries: control.cancel kills the process tree and waits for observed exit. Resume, interrupt, and steer remain unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Cancelled deliveries remain uncertain and cannot auto-replay; a missing local process raises RuntimeTargetError("nothing running"), recorded as failed when durable delivery evidence still identifies a target. Bridge targets already known to be absent or stale retain nothing_running/stale_target outcomes. Durable fresh-session catch-up provides continuity but does not itself stop a provider invocation.
