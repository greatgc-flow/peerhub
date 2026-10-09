# Fixtures / Fakes / Test Harness

## Core deterministic fixtures
- `ManualClock`: `now()/advance()`; eliminates wall-clock sleep in lease/TTL.
- `SequentialIdSource`: deterministic ids + observation capture ordinal.
- `WorkspaceFactory`: `fresh()/reopen()/replace_generation()`.
- `ConnectionFactory`: independent SQLite connection per writer.
- `Barrier/Latch`: append/CAS/claim/migration named race point.
- `StrictJsonFixture`: JSON value generator; NaN/Infinity/non-JSON rejection.

## Multi-process harness
Uses actual OS processes to reproduce append/idempotency/Offset/claim races. Each child uses a separate connection/process identity and has a bounded join timeout + diagnostic dump. Do not induce a winner using `sleep()`.

## Faultable storage/VFS
Deterministic injection points:
```text
sqlite.busy
sqlite.full_before_commit
sqlite.readonly
sqlite.corrupt_fixture
migration.before_commit
```
Use only on disposable copies, not the actual production DB.

## `FakeRuntimeTarget`
Scripted events:
```text
CREATE_SESSION(id)
RESUME_SESSION(ok | missing | unsupported | rejected)
PRESPAWN_ERROR
STARTED(execution_id)
OUTPUT(chunk)
TERMINAL(response)
TERMINAL_DUPLICATE_SAME(response)
TERMINAL_DUPLICATE_DIFFERENT(response2)
INTERRUPT_OK
INTERRUPT_UNSUPPORTED
TERMINATE_ERROR
TIMEOUT_AFTER_START
DISCONNECT_AFTER_START
ERROR(code/message)
```
The call log preserves create/resume/deliver/interrupt/terminate/finalize order.

## `CrashInjector`
```text
append.before_commit
append.after_commit_before_return
bridge.after_runtime_start
bridge.after_partial_output
bridge.after_terminal_evidence_before_offset_ack
bridge.after_reconcile_commit
claim.after_acquire
claim.after_expiry_before_takeover
restore.after_generation_change
migration.before_commit
```

## `FakeObservationSource`
Scripts MEASURED/ABSENT/UNAVAILABLE/ERROR, malformed/timeout, source clock skew, captured_at missing/equal-time.

## Snapshot helpers
- Core logical state digest (canonical row order + SHA-256)
- authoritative workspace file snapshot (excludes SQLite transient lock/shm)
- runtime call log
- release evidence manifest/checksum

Diag uses the logical state digest + runtime/provider call count 0 as the final read-only oracle.
