# Fault Injection Matrix

> Deterministic fault cases: **21**

| Test | Injection | Required recovery oracle |
|---|---|---|
| `FLT-001` | raise/terminate before commit barrier | No uncommitted Record is visible. A subsequent clean append commits exactly once with a unique position greater than every previously committed position; a numeric gap is allowed. |
| `FLT-002` | raise after commit before client success | Retry returns committed Record; count remains 1. |
| `FLT-003` | disconnect immediately after process start | Execution evidence state is MAY_HAVE_STARTED; delivery is not auto-replayed. |
| `FLT-004` | none | No second runtime start occurs until explicit reconciliation/operator/control decision. |
| `FLT-005` | raise after evidence commit before offset CAS | Bridge recognizes terminal evidence/idempotency and advances/reconciles without re-executing runtime. |
| `FLT-006` | terminate bridge after start evidence | Recovered certainty is STARTED/MAY_HAVE_STARTED per evidence, never downgraded to NOT_STARTED. |
| `FLT-007` | advance manual clock beyond lease expiry | Finalize is fenced; system records uncertainty/evidence without allowing stale Offset advancement. |
| `FLT-008` | workspace generation switch | G1 claim/session cannot be used; fresh claim/session generation is required. |
| `FLT-009` | database write lock | Either append commits fully after wait or explicit busy failure occurs; never partial row/offset corruption. |
| `FLT-010` | source timeout/malformed payload | State is ERROR or UNAVAILABLE with evidence; numeric quota/health is not fabricated. |
| `DIA-005` | malformed observation payload | Affected section reports explicit error/unknown while unrelated sections remain readable; DB is unchanged. |
| `DIA-006` | read I/O error | Diag exits/reports failure; no migration/repair/restart/write is attempted. |
| `FLT-011` | inject SQLITE_FULL / write failure before commit | Append reports explicit failure; after reopen there is either complete prior state or complete new commit, never partial rows/idempotency/offset. |
| `FLT-012` | inject readonly VFS/filesystem | Explicit read-only failure; original database bytes/content remain readable after reopening read-only. |
| `FLT-013` | corrupt disposable DB bytes | Core open/Diag reports corruption; no empty replacement DB, migration, repair or write occurs. |
| `BRG-017` | all session acquisition attempts fail | Bridge exits with explicit failure/uncertainty after configured attempts; no unbounded loop or duplicate Records. |
| `CTL-002` | terminate/kill raises | Cancel Record remains committed; failure evidence explicit; no rollback of durable history/Offset. |
| `OBS-012` | probe timeout/exception | At most explicit Observation evidence changes; Peer/Stream/Record/Offset and routing/runtime invocations remain unchanged. |
| `DIA-009` | log open raises permission/IO error | Core/Observation sections render; log section reports explicit unavailable/error; no chmod/repair/write attempted. |
| `FLT-014` | late terminal callback to stale generation | A is fenced before any response Record/Offset mutation; uncertainty/late evidence may be recorded only through safe non-authoritative evidence path. |
