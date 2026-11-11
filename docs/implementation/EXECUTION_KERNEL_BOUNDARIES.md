# Execution Kernel boundary — architecture decision for #335

**Status:** Implemented incrementally on `refactor/core-kernel-boundaries-20261111`; release/merge pending validation.  
**Date:** 2026-11-11. **Parent:** [#335](https://github.com/greatgc-flow/peerhub/issues/335).  
**Baseline:** earlier v0.24.4 code review 83.6/100 (rounded 84). This is **not** a validated score for this branch.

## Decision

Keep the public **communication Core** narrow and durable: Peer, Stream, Record, Offset.
Define the existing Bridge/Claims/Registry as a **logical Execution Kernel**, not a new
package, OS-level scheduler or service. Its ports may be made independent of the
concrete implementation when this removes real import coupling.

```text
CLI (input, errors, presentation)              Other Extensions (memory/diag/search)
    |                                                      |
    v                                                      v
ask facade ---> Bridge + ClaimStore + SessionRegistry ---> CoreStore (facts, CAS, read UoW)
                         ^                                     ^
                         |                                     |
              RuntimeTarget protocol                   Records projections
                         ^
                         |
      Provider adapters (cc / cx / ag; launch, parse, resume)
```

The arrow from adapters points to the **neutral runtime port**, not the Bridge.
Kernel owns durable execution truth; adapters report events but do not rewrite
certainty, delivery state or session registry. Other extensions read authoritative
Core records through public Core operations instead of depending on private SQLite
row mappers.

### Ownership / explicit non-ownership

| Responsibility | Single owner | Must not own |
|---|---|---|
| Peer/Stream/Record/Offset, canonical serialization, CAS, snapshot reads, schema guard | `peerhub/core/` | provider subprocesses, profile/permission selection |
| Claim lease, fenced completion, invocation marker, delivery certainty, replay/reconcile | `bridge_claims.py` + `bridge.py` | CLI presentation or vendor-specific stdout grammar |
| Retained session identity/generation, eligibility, tombstones | `bridge_registry.py` | per-vendor credential discovery |
| Runtime protocol, runtime event and error types | `runtime_contract.py` | stored state, network I/O or provider launch |
| Provider launch, output parsing, account-specific OS behavior | `extensions/adapters/` | authoritative delivery/session truth |
| Immutable history-driven memory/search and read-only diagnostics | capability extensions | implicit model invocation from snapshot reads |
| User-facing commands, `--json` and hints | `cli/` | persistence contract/uncertain execution decision |

Do not move SQL tables across owners or rename their durable identifiers. No new
`kernel/` tree until a demonstrable duplicated responsibility or dependency cycle
requires one.

## Concrete code changes in this branch

1. **Provider-neutral port:** Move `RuntimeTargetError`, `PrespawnError`,
   `ContextLostError`, `RuntimeEvent`, `RuntimeTarget` and `SteerableTarget`
   into `extensions/runtime_contract.py`. The existing `extensions.bridge`
   import paths **re-export the identical objects** for existing consumers.
   Adapters import the port directly, removing their concrete-Bridge import
   dependency (no vendor behavior or stream state machine changes).
2. **Core-owned consistent history scan:** Add
   `CoreStore.iter_records_snapshot(page_size=500)`, an ordered (stream ID,
   position) generator backed by a **single pinned read transaction**. Delegate
   `extensions.source_records.source_records()` to that API. This removes
   extension-private mapping (`CoreStore._row_to_record`) and duplicates of
   SQL paging without altering the extension entry point.
3. **Regression gates:** Add offline tests for historical type identity,
   adapter cold import, forbidden Core→Extension/CLI imports, immutable snapshot
   under a concurrent WAL writer, paging/order parity and invalid page sizes.

The current `bridge.py` is large but **file length is not a refactoring goal**.
Avoid moving certainty transitions or splitting the state machine until an
independent interface and a failure-proofing test require it.

## Behavior / certainty guardrails

- **Exact replay:** same scoped immutable request identity returns original
  durable result; a changed prompt/binding/session selector conflicts.
- **No unsafe retry:** `MAY_HAVE_STARTED` and unresolved invocation evidence
  are not permission to execute again. Reconciliation must be explicit.
- **Session semantics:** default latest-compatible retained session; `fresh`
  generates a new session (with bounded Stream catch-up); exact selection
  rejects mismatch instead of silently substituting another session.
- **Fenced writes:** claims and workspace restore lineage gate delivery effects,
  response append, offset acknowledgement, and terminal finalization.
- **Read-only:** diagnostic snapshots cannot probe providers or mutate data;
  unmeasured quotas/context remain UNKNOWN or STALE.
- **Batch:** writable overlap safety continues to prevail over convenience.
  AGY parallel restriction is tracked separately by
  [#323](https://github.com/greatgc-flow/peerhub/issues/323).

## Verification and objective scoring

Offline tests:
```sh
python -m pytest -q tests/communication/bridge/test_runtime_contract_boundary.py tests/communication/bridge/test_source_records_boundary.py
python -m pytest -q tests/continuity/test_m2_m3_exit_gates.py
python -m pytest -q
pyright
python tools/command_inventory.py --check
python tools/traceability.py --upto 9
```

Run full candidate-matched CI and provider live gate only under established
repository policy; canaries cost provider quota. Evidence must identify commit
SHA, OS, Python and exact passed/failed/skipped counts. This document records
requirements, **not a claim that any listed command has passed**.

For a 90/100 grade, independently re-score ten unchanged criteria (equal weight):
Core purity, execution ownership, transactional safety, session reuse,
adapter independence, concurrency policy, observability, failure recovery,
VerySimple maintainability and test/release evidence. Changes to this branch
directly target execution ownership (78), adapter independence (84), Core
encapsulation (95) and maintainability (62). More improvement may be required:
a score target is **not proof** of reaching it. Rescore only after CI gates and
non-regression evidence, recording at least one concrete reference per increase.

## Non-goals, rollback and follow-ups

- No DB schema change or migration; no CLI surface or JSON changes.
- No change to certainty, claims, vendor invocation, quotas or native session handling.
- No speculative removal of historical evidence and no automatic main merge.
- If a regression appears, revert the three production-file changes
  (`runtime_contract.py`, `bridge.py`, `adapters/base.py`) as a group;
  separately revert Core snapshot delegation (`core/store.py`,
  `extensions/source_records.py`) as a group. Each is designed to be
  independently reviewable and uses existing compatibility entry points.
- Investigate Bridge DDL initialization/lock cost as a **measurement** under
  [#335](https://github.com/greatgc-flow/peerhub/issues/335) and existing
  [#282](https://github.com/greatgc-flow/peerhub/issues/282);
  no claim of an established bottleneck.
- Consult [#327](https://github.com/greatgc-flow/peerhub/issues/327)
  for historical docs, SSOT and separate VerySimple cleanup decisions.
