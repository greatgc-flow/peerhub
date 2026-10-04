# Fixtures / Fakes / Test Harness

## Core deterministic fixtures
- `ManualClock`: `now()/advance()`; lease/TTL에 wall-clock sleep 제거.
- `SequentialIdSource`: deterministic ids + observation capture ordinal.
- `WorkspaceFactory`: `fresh()/reopen()/replace_generation()`.
- `ConnectionFactory`: writer별 독립 SQLite connection.
- `Barrier/Latch`: append/CAS/claim/migration named race point.
- `StrictJsonFixture`: JSON value generator; NaN/Infinity/non-JSON rejection.

## Multi-process harness
실제 OS process를 사용하여 append/idempotency/Offset/claim race를 재현합니다. 각 child는 별도 connection/process identity를 사용하고 bounded join timeout + diagnostic dump를 가집니다. `sleep()`으로 winner를 유도하지 않습니다.

## Faultable storage/VFS
결정론적 injection points:
```text
sqlite.busy
sqlite.full_before_commit
sqlite.readonly
sqlite.corrupt_fixture
migration.before_commit
```
실제 운영 DB가 아니라 disposable copy에서만 사용합니다.

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
Call log는 create/resume/deliver/interrupt/terminate/finalize 순서를 보존합니다.

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
MEASURED/ABSENT/UNAVAILABLE/ERROR, malformed/timeout, source clock skew, captured_at missing/equal-time를 script합니다.

## Snapshot helpers
- Core logical state digest (canonical row order + SHA-256)
- authoritative workspace file snapshot (SQLite transient lock/shm 제외)
- runtime call log
- release evidence manifest/checksum

Diag는 logical state digest + runtime/provider call count 0을 최종 read-only oracle로 사용합니다.
