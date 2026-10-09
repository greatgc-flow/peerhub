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

Do not create separate Core domains for Task/Room/Thread.

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

`position` defines the canonical durable order.

## Offset

```text
peer_id
stream_id
read_through_position
revision
```

Offset represents only the read/delivery position, not work completion.

## Core invariant
Core does not import Extensions.
