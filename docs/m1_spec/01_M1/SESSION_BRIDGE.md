# Session Bridge

## 책임
- deliver Records to runtime;
- create/resume provider session;
- stream output;
- interrupt/steer when supported;
- append response Record;
- capture execution evidence.

## Provider/session mapping

```text
peer_id
stream_id
runtime_kind
external_session_id
session_generation
adapter_fingerprint
model/profile binding
resumable
state
last_seen
```

## Single-active delivery claim
두 Bridge가 같은 unread Record를 중복 실행하지 않도록:

```text
(workspace, stream, peer)
owner
generation
heartbeat/expires
```

만 둡니다. 기존 generic lease platform은 가져오지 않습니다.

## Execution certainty
기존 시행착오에서 유용했던 의미론을 축소 재사용:

```text
NOT_STARTED
MAY_HAVE_STARTED
STARTED
TERMINAL
```

`MAY_HAVE_STARTED`는 blind replay 금지.

## 미래 Harness
OpenAI Agents API/SDK, Claude Managed Agents, Microsoft Harness, ADK 등은
Session Bridge의 `RuntimeTarget` adapter가 됩니다.

## M1 Spec Alignment (2026-10-09)
CLI adapters support native terminate for active local deliveries: control.cancel kills the process tree and waits for observed exit. Resume, interrupt, and steer remain unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Cancelled deliveries remain uncertain and cannot auto-replay; a missing local process raises RuntimeTargetError("nothing running"), recorded as failed when durable delivery evidence still identifies a target. Bridge targets already known to be absent or stale retain nothing_running/stale_target outcomes. Durable fresh-session catch-up provides continuity but does not itself stop a provider invocation.
