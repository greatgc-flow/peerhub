# Stream / Record / Offset

## Ordering
기존 Room처럼 `MAX(seq)+1`을 application에서 계산하지 않습니다.
SQLite transaction이 부여하는 monotonically increasing append position을 SSOT로 사용합니다.

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

Exactly-once DB append와 exactly-once AI execution을 혼동하지 않습니다.

## Control Records

```text
control.pause
control.resume
control.redirect
control.cancel
context.boundary
```

Control은 durable user intent이고 실제 interrupt/steer는 Session Bridge 책임입니다.

## M1 Spec Alignment (2026-10-09)
Current adapters report native resume/interrupt/terminate/steer as unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Durable fresh-session catch-up is the continuity mechanism; it does NOT stop an already-running provider invocation, and native control stays an optional capability per adapter.
