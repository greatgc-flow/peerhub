# Persistence / Concurrency

## Core store
Keep SQLite as the authoritative local store.

Recommended:
- WAL;
- foreign_keys;
- busy timeout;
- explicit ordered migrations;
- transaction/UoW;
- read-only UoW;
- workspace identity/generation.

## Core tables (conceptual)

```text
peers
streams
stream_members
records
offsets
```

Extensions own their tables, and Core startup does not require Extension tables.

## Crash tests
- before commit;
- after commit/before client success;
- after runtime started;
- before Offset ack;
- stale Bridge owner;
- restore generation change.

## Existing Assets
Reuse the existing PeerHub experience with SQLite UoW/event offset/CAS/online backup/process cancellation,
but do not carry over the current orchestration schema itself.
