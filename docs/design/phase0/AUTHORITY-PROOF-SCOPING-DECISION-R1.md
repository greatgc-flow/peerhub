# Authority Proof Scoping Decision R1

**Status:** `cc` judgment record reconciling two independent scoping
proposals (`ag.deepthink`, `cx.deepthink`) for how to turn
`AUTHORITY-PROOF-FIXTURE-COMPANION-R1.md`'s coarse AC-01..AC-09 subject
binding into concrete, scriptable fixture IDs. Documentation-only; carries
no implementation or ratification authority by itself. Both peers
unanimously ACKed this resolution.

## Why this needed its own round

`AUTHORITY-CUTOVER-CONTRACT.md` §6's proof matrix lists multiple distinct
sub-requirements per AC area (e.g. Filesystem alone needs NTFS success,
exFAT/FAT/network rejection, WAL/lock capability failure, redirected/
virtualized-but-NTFS-reporting rejection, and multi-alias-to-one-identity
resolution — five materially different fault surfaces). The companion
document already bound each AC-0X to its subject area; it did not yet
break any of them into scriptable sub-fixtures, and none has an event
script, oracle, or capture. This area also governs a real future authority
handoff from `hub.py` to PeerHub — a materially higher blast radius than
the 54 behavioral IDs and the 27 QP/ST/ED IDs, which govern only internal
PeerHub decision logic.

## Where ag and cx converged

- The existing `DomainOracle`/`DomainSubjectAdapter` registry, closed
  schemas, canonical comparison, and conjunctive capture gate remain the
  sole verification framework — no new architecture needed at that layer.
- OS-kernel-level races (retained-handle custody, PID/process-birth
  identity, real volume/file-ID stability) cannot be faithfully proven by
  any abstract model in Python.
- AC-04's marker CAS scenarios need a real isolated SQLite subject adapter
  (same precedent as `GB-01`), not an in-memory simulator.
- Do not implement all 9 areas in one continuous push.

## Where they disagreed, and the resolution

1. **Module architecture.** `ag` proposed one generic AC input shape
   (`environment_state` + `transition_operation`) with a single oracle
   computing `(state, operation) -> (new_state, result, error)`, applied
   uniformly across all 9 areas. `cx` proposed separate, narrowly-scoped
   modules per area (`authority_filesystem.py`, `authority_identity.py`,
   `authority_fence.py`, etc.), each owning only its own state, explicitly
   rejecting one shared fake-filesystem/lock/epoch "platform." **Resolution:
   cx.** A single generic model risks proving only that the simulator
   internally agrees with itself (the same circularity concern this
   project has flagged before, e.g. the rejected generic settings
   key/value table and the rejected generic domain-event-type extension);
   narrow per-area modules match the pattern already validated five times
   in `domain/`.
2. **What "provider-free" means.** `ag`'s framing implicitly treated
   everything infeasible to model abstractly as permanently deferred
   (`V1_SPEC_ONLY` forever, tagged `OS_KERNEL_INTEGRATION_REQUIRED`). `cx`
   corrected this: "provider-free" means no external AI-vendor call, not
   "no real OS." An abstract fixture can only prove "given these
   observations, PeerHub picks the correct safe outcome" — never that
   Windows actually produces those observations. **Resolution: cx.**
   Cutover eligibility requires **two separate tracks**: abstract-model
   fixtures (decision-correctness, buildable now) and later real-OS
   integration fixtures using disposable files/processes/volumes (
   observation-correctness, a distinct future round) — not one track with
   a permanent exemption for the other.
3. **Fixture granularity.** `ag`'s AC-01 breakdown used 5 sub-fixtures,
   collapsing exFAT+FAT into one and WAL-failure+lock-failure into
   another. `cx`'s breakdown used 8, keeping each fault surface separate
   ("must not be merged... because the fault surface is different").
   **Resolution: cx** — finer granularity here has a real cost/benefit
   case (each merged pair would hide which specific fault a captured
   fixture actually proves).
4. **Unratified specifics.** `ag` asserted five concrete error-code
   strings (`UNSUPPORTED_FILESYSTEM_VOLUME`, `WRITER_EPOCH_STALE`,
   `EPOCH_CHANGED_DURING_PREPARE`, `MARKER_CAS_CONTESTED`,
   `MIGRATION_LOCK_RENEWAL_LOST`) as if already settled. `cx` found the
   contract does not actually freeze a public error code for AC-01-08's
   lock-contention outcome or for AC-04-02/03's stale-write rejection, and
   declined to invent one. **Resolution: cx** — this is the same pattern
   already correct every time it has come up this session (`RT-05`'s
   formula, `QP-04`'s reset-boundary rule, `ST-03/05/07`'s representations).
   These two items are recorded as open freeze items below, not invented.
5. **Cadence.** `ag` proposed 3 phases of 3 areas each (3 checkpoints).
   `cx` proposed one area per fully-reviewed increment (9 checkpoints:
   freeze IDs → ratify design → implement → adversarial review → capture →
   stop), plus a separately-ratified **composed** scenario after the first
   5 areas (identity → drain → custody → marker CAS) to test that the
   isolated per-area models actually compose safely — "passing isolated
   unit models does not prove their interfaces compose safely." **Resolution:
   cx** — matches the user's explicit direction to proceed carefully
   rather than quickly for this specific area, and the composed-scenario
   step is a real systems-safety point `ag`'s phasing didn't address.

## Adopted plan

**Order:** AC-01 (Filesystem) → AC-02 (Identity) → AC-04 (Fence) → AC-05
(JSON crash/custody) → AC-08 (Drain) → **composed integration scenario** →
AC-03 (Shadow) → AC-06 (External effect) → AC-07 (Recovery) → AC-09
(Quota).

**Sub-fixture ID convention:** `AC-0X-YY` (two-digit sub-case index),
fault-injected negatives as `AC-0X-YY-NEG-01`.

**AC-01 (Filesystem) — 8 sub-fixtures**, from `cx`'s worked breakdown:
`AC-01-01` local-NTFS success; `AC-01-02` exFAT rejection; `AC-01-03` FAT
rejection (separate from exFAT — different platform detection path);
`AC-01-04` network/SMB-share rejection; `AC-01-05` WAL/shared-memory
capability failure; `AC-01-06` exclusive-lock/rename/custody capability
failure (separate from WAL failure); `AC-01-07` redirected/virtualized
storage that reports NTFS (oracle must trust the resolved physical node,
never the presented label); `AC-01-08` two alias-resolution paths to one
physical volume/file-ID resolving to exactly one identity and one lock,
first acquisition succeeds and the second contends. **Open freeze item:**
`AC-01-08`'s external lock-contention error code is not yet named in the
contract; do not invent one before scripting it.

**AC-04 (Fence) — 6 sub-fixtures**, from `cx`'s worked breakdown:
`AC-04-01` a still-valid pre-marker write under an unchanged epoch
legitimately commits (the epoch fence does not reject every pre-marker
write — drain/rehash/custody are the mechanisms that close that race, not
the epoch check alone); `AC-04-02` a write whose lease epoch is already
behind the committed marker epoch is fenced with zero mutation;
`AC-04-03` an epoch change between an earlier check and the final
pre-commit recheck is caught by the mandatory final recheck, not the
earlier one; `AC-04-04` two CAS contenders at the same admission
phase/epoch — exactly one marker commits, the loser gets
`CUTOVER_EPOCH_CONTENDED`; `AC-04-05` a contender whose admission snapshot
is stale by the time of its own marker transaction is rejected the same
way; `AC-04-06` migration-lock renewal loss before marker commit produces
`MIGRATION_LOCK_LOST` with no epoch transition and no retry under the same
admission record. `AC-04-04`/`AC-04-05` use an isolated SQLite subject
adapter for genuine CAS evidence, per the `GB-01` precedent. **Open freeze
item:** the stale-legacy-write rejection error code for `AC-04-02`/
`AC-04-03` is not yet named in the contract.

**Non-abstractable items (real future integration evidence required,
not modeled here):** real final-path resolution through junctions/reparse
points/`subst`/mapped drives; actual NTFS volume-GUID/file-ID stability;
real SQLite WAL/shared-memory locking on the resolved volume; actual
Windows migration-lock custody and renewal; `CreateFileW`
(`dwShareMode=0`) and already-open `FILE_SHARE_DELETE` handle semantics;
PID-reuse/process-birth identity across real process crashes. These may
still get abstract specification fixtures now, but such fixtures alone
can never promote the area past `V1_SPEC_ONLY`.

## Status

`authority-proof-status-v1.json`'s AC-01..09 remain `V1_SPEC_ONLY`,
`cutover_execution_eligible: false` throughout this entire plan. Model
captures under this plan are acceptance-harness evidence for PeerHub's own
decision logic — never authority-cutover evidence, never claimed
production-behavior evidence, and never sufficient alone for cutover
execution eligibility.

## Final Call

Both `ag.deepthink` and `cx.deepthink` ACKed this resolution before AC-01
work begins. User approved proceeding with AC-01 first under this plan
(not the faster/coarser alternatives offered).
