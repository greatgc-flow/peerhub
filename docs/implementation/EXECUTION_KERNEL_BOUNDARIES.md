# Execution Kernel boundaries

Architecture decision for [#335](https://github.com/greatgc-flow/peerhub/issues/335).
Open follow-ups live in issues (e.g. [#323](https://github.com/greatgc-flow/peerhub/issues/323) AGY isolation).

## Decision

Keep the public **communication Core** narrow and durable: Peer, Stream, Record, Offset.
Treat the existing Bridge/Claims/Registry as a **logical Execution Kernel**, not a new
package, OS-level scheduler or service. Its ports are independent of concrete
implementations where that removes real import coupling.

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

Adapters depend on the **neutral runtime port**, not the Bridge. The Kernel owns durable
execution truth; adapters report events but never rewrite certainty, delivery state or
the session registry. Other extensions read Core records through public Core operations,
not private SQLite row mappers.

## Ownership

| Responsibility | Single owner | Must not own |
|---|---|---|
| Peer/Stream/Record/Offset, canonical serialization, CAS, snapshot reads, schema guard | `peerhub/core/` | provider subprocesses, profile/permission selection |
| Claim lease, fenced completion, invocation marker, delivery certainty, replay/reconcile | `bridge_claims.py` + `bridge.py` | CLI presentation, vendor stdout grammar |
| Retained session identity/generation, eligibility, tombstones | `bridge_registry.py` | per-vendor credential discovery |
| Runtime protocol, runtime event and error types | `runtime_contract.py` | stored state, network I/O, provider launch |
| Provider launch, output parsing, account-specific OS behavior | `extensions/adapters/` | authoritative delivery/session truth |
| History-driven memory/search, read-only diagnostics | capability extensions | implicit model invocation from snapshot reads |
| Commands, `--json`, hints | `cli/` | persistence contract, uncertain-execution decisions |

Do not move SQL tables across owners or rename durable identifiers. No `kernel/` tree
until a demonstrated duplicated responsibility or dependency cycle requires one. File
length of `bridge.py` is not a refactoring goal; do not split the certainty state
machine without an independent interface and a failure-proofing test.

## Concrete boundaries in code

- **Runtime port:** `RuntimeTargetError`, `PrespawnError`, `ContextLostError`,
  `RuntimeEvent`, `RuntimeTarget`, `SteerableTarget` live in
  `extensions/runtime_contract.py`; `extensions.bridge` re-exports the identical objects.
- **Pinned history scan:** `CoreStore.iter_records_snapshot(page_size=500)` yields
  (stream ID, position)-ordered records from a single pinned read transaction;
  `extensions.source_records.source_records()` delegates to it.
- **Gates:** offline tests cover type identity, adapter cold import, forbidden
  Core→Extension/CLI imports, snapshot immutability under a concurrent WAL writer.

## Behavior guardrails

- **Exact replay:** the same scoped immutable request identity returns the original
  durable result; a changed prompt/binding/session selector conflicts.
- **No unsafe retry:** `MAY_HAVE_STARTED` and unresolved invocation evidence never
  permit re-execution; reconciliation is explicit.
- **Sessions:** default is latest-compatible retained session; `fresh` creates a new one
  (bounded Stream catch-up); exact selection rejects mismatch instead of substituting.
- **Fenced writes:** claims and workspace-restore lineage gate delivery effects,
  response append, offset acknowledgement and terminal finalization.
- **Read-only diagnostics:** snapshots never probe providers or mutate data; unmeasured
  quotas/context stay UNKNOWN or STALE.
- **Batch:** writable-overlap safety wins; scoped claims are not workspace isolation (#323).

## Bridge trigger reconciliation

Core, ClaimStore and Bridge install protection triggers that keep history immutable
(UPDATE/DELETE/REPLACE rejected even with `recursive_triggers` OFF). Reinstalling them on
every open churned the schema (72 SQLite schema-cookie increments per warm open), so
initialization now reconciles instead:

1. **Exact comparison.** Inside the existing serialized write transaction, compare trigger
   **names and complete stored SQL** from `sqlite_master` against the expected set. Only the
   `IF NOT EXISTS` after `CREATE TRIGGER` is stripped to match SQLite's stored form; never
   normalize body whitespace or quoted literals.
2. **Match means no DDL.** An unchanged reopen issues no DROP/CREATE (schema churn 72 → 0).
3. **Full rebuild on any mismatch.** A missing, modified, extra or legacy definition
   replaces the complete owned set; no per-trigger patching.
4. **Atomic.** Repair runs inside the init transaction, so a failed install or registry
   step rolls back with no half-installed protection.
5. **Quoted identifiers.** Embedded quotes in legacy trigger names are escaped when
   dropping (a legacy name with `"` previously broke reopen).
6. **Legacy registry** (single-slot layouts) keeps the original drop/reinstall migration.
7. **No cached shortcut.** Registry init, projection repair and migration hooks still run
   on every open. Unrelated tables' triggers are untouched.

Coverage: `tests/communication/bridge/test_bridge_init_reconciliation.py` (every trigger
missing/changed, quoted legacy names, rollback, parallel opens, future-schema refusal).

## Measurement

Methodology and limits (disposable databases, paired runs, lock-wait included, no SLA)
are in [claim-init benchmark](../benchmarks/claim-init-2026-11-11.md); reproduce with
`python -m tools.bridge_init_bench --rounds 10`. The deterministic effect is avoided
schema churn, not a universal speed claim. The Bridge DDL/lock cost is tracked under
#335 and [#282](https://github.com/greatgc-flow/peerhub/issues/282).

## Non-goals and rollback

- No DB schema change or migration; no CLI or JSON surface change.
- No change to certainty, claims, vendor invocation or session handling.
- Revert groups independently: `runtime_contract.py` + `bridge.py` + `adapters/base.py`;
  Core snapshot delegation (`core/store.py`, `extensions/source_records.py`); trigger
  reconciliation (`bridge.py` + its test and inventory entry). Never rewrite user data.
