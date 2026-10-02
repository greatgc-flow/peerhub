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
