# Long-Horizon Continuity

핵심:

```text
Stream ≠ Session ≠ Context ≠ Process ≠ Harness
```

## 진행현황 질문
Record → operational event → observation → runtime event 순으로 확인하고,
부족할 때만 AI에게 직접 질문하여 quota를 소비합니다.

## Session 소실
fresh session + Stream catch-up.

## Context limit/compaction
정상 lifecycle로 취급. 전체 transcript 재주입이 아니라 bounded catch-up projection.

## Pause
pause Record 저장 → best-effort interrupt.
`PAUSED != ROLLED_BACK`.

## Resume
compatible session 우선, 실패 시 fresh generation.

## Redirect
같은 goal이면 same Stream.

## New direction
goal 자체가 달라지면 new Stream. 기존 Stream은 history/reference로 남김.

## Quota exhaustion
Core state 불변. Observation만 unavailable/exhausted로 갱신.
자동 Peer 전환은 미래 Routing/Orchestration extension.

## M1 Spec Alignment (2026-10-09)
Current adapters report native resume/interrupt/terminate/steer as unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Durable fresh-session catch-up is the continuity mechanism; it does NOT stop an already-running provider invocation, and native control stays an optional capability per adapter.
