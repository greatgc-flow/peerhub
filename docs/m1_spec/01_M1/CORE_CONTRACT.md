# Core Contract

## Peer
stable collaboration identity.

```text
peer_id
display_name?
adapter_ref?
metadata
created_at
```

`Peer != CLI kind != profile != process != session`.

## Stream

```text
stream_id
title?
state = OPEN | CLOSED
members[]
revision
created_at
metadata
```

Task/Room/Thread를 독립 Core domain으로 만들지 않습니다.

## Record

```text
record_id
stream_id
position
author_peer_id
kind
body
targets[]
reply_to?
refs[]
metadata
idempotency_key
payload_digest
created_at
appended_at
schema_version
```

`position`이 canonical durable order입니다.

## Offset

```text
peer_id
stream_id
read_through_position
revision
```

Offset은 읽음/전달 위치일 뿐 작업 완료가 아닙니다.

## Core invariant
Core는 Extension을 import하지 않습니다.
