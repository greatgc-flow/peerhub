# Persistence / Concurrency

## Core store
SQLite를 authoritative local store로 유지합니다.

권장:
- WAL;
- foreign_keys;
- busy timeout;
- explicit ordered migrations;
- transaction/UoW;
- read-only UoW;
- workspace identity/generation.

## Core tables (개념)

```text
peers
streams
stream_members
records
offsets
```

Extension은 자기 table을 소유하고 Core startup이 Extension table을 요구하지 않습니다.

## Crash tests
- before commit;
- after commit/before client success;
- after runtime started;
- before Offset ack;
- stale Bridge owner;
- restore generation change.

## 기존 자산
현재 PeerHub의 SQLite UoW/event offset/CAS/online backup/process cancellation 경험은
재사용하되 현재 orchestration schema 자체는 그대로 가져오지 않습니다.
