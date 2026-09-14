# PeerHub authority cut-over contract (Phase 0 draft)

> Status: **draft; implementation is forbidden until R:10 ratification.**
> Scope: the future replacement of Engram `hub.py` communication and
> coordination authority by a PeerHub workspace database. This is not a
> general filesystem migration mechanism and does not authorize a cut-over.

## 1. Non-negotiable safety rule

At every instant, each fact has exactly one live write authority. There is no
dual-writer interval and no "last writer wins" reconciliation. A failed or
ambiguous transition preserves evidence and stops safely; it never invents a
completion or replays an external effect.

The supported v1 authority phases are:

| Phase | Authoritative writer | Permitted PeerHub behavior | Exit condition |
|---|---|---|---|
| `ENGRAM_AUTHORITY` | Engram legacy files | read-only inventory and characterization | all Phase 0 gates pass |
| `SHADOW_VALIDATE` | Engram legacy files | read/translate/compare only; no state or provider effect | consecutive equivalent comparisons and no unresolved drift |
| `CUTOVER_DRAINING` | Engram until fence commit | admission closed; bounded drain and validation only | all leases terminal or safe-aborted |
| `PEERHUB_AUTHORITY` | PeerHub SQLite state | sole operational writer; legacy access is read-only compatibility | rollback window ends only after evidence review |
| `RETIRED` | PeerHub SQLite state | no legacy writer or compatibility mutation path | separately ratified retirement record |

`CUTOVER_DRAINING` is intentionally not a normal operating phase. Startup or
recovery that finds it without a complete durable transition receipt returns
to a conservative recovery procedure, not normal dispatch.

## 2. Supported v1 boundary and workspace identity

V1 supports **NTFS plus SQLite WAL only**. Before creating/opening a database,
PeerHub performs a capability probe. The probe establishes identity and
semantics on the resolved volume, never on a path string or a reported
filesystem name. It resolves the final target through every reparse point,
junction, symbolic link, `subst` alias, and mapped network drive, and records
the NTFS volume GUID together with the file ID of the home directory. A
redirected or virtualized path that resolves off local NTFS fails with
`FILESYSTEM_UNSUPPORTED` even when it reports NTFS. The OS migration lock is
keyed on this resolved identity, so two aliases for one physical home collide
on one lock. FAT/exFAT, network/share semantics, or any filesystem which
cannot provide the required lock/rename/WAL guarantees fails with
`FILESYSTEM_UNSUPPORTED` before any database, marker, legacy file, or provider
effect is changed. A non-NTFS backend is a future, separately designed feature.

One `PeerHubHome` belongs to one resolved workspace identity and one immutable
random `workspace_home_id`. The resolved volume-and-file-ID identity, home ID,
and database identity are persisted and bound together. Opening the same home
through a mismatched identity or opening an identity through a different home fails closed with
`WORKSPACE_IDENTITY_MISMATCH`. Clone/relocation is an explicit audited export
and import procedure; it is never inferred from a copied directory.

The workspace database owns workspace-local coordination, configuration, and
authority state only. Provider-account quota remains adapter-provided external
evidence. V1 neither reserves nor allocates a provider quota across multiple
workspaces. A manually authorized dispatch is therefore distinct from a
routing recommendation. Any cross-workspace quota coordinator requires a
separate design and authority model.

## 3. Shadow validation and frozen inputs

In `SHADOW_VALIDATE`, Engram remains the only writer. PeerHub may derive a
candidate command/result from a read-only snapshot, but it must not mutate
PeerHub operational state, legacy state, or a provider. Every change to a
compared source/configuration revision resets the consecutive-equivalence
streak; results from different revisions are never pooled.

The transition request declares an exhaustive legacy JSON write scope. At
lease admission it records the SHA-256 of every file in that scope, the
baseline source/configuration digests, canonical workspace identity, and the
candidate PeerHub import digest. Immediately after the drain and immediately
before authority-marker commit it re-hashes every frozen source/configuration
input and every declared legacy JSON file. Any difference aborts the cut-over
with `CUTOVER_INPUT_DRIFT`; no authority marker is written.

This check is mandatory even for a config-only cut-over: a legacy process can
write JSON and crash before it records a SQLite terminal receipt. Without the
admission and pre-commit file hashes, a force-abort could silently lose that
write.

## 4. Authority fence, leases, and bounded drain

The transition has a durable monotonic `authority_epoch` and a shared
authority fence. Every legacy mutation lease records its epoch at admission.
Each legacy writer must re-check both its lease validity and the current epoch
immediately before committing any state write; a mismatch rejects the write
without retrying it under a new epoch.

Cut-over acquires a short external OS migration lock (15-second renewable
lease with owner PID plus process-birth identity) and uses SQLite transactions
with a 5-second busy timeout for short database critical sections. Loss or
non-renewal of the OS migration lock before the marker transaction commits
aborts the attempt with `MIGRATION_LOCK_LOST`; it is never retried under the
same admission record. The lock is a liveness optimization and confers no
authority. Neither lock is a substitute for the epoch check at legacy commit.

The drain admits no new legacy mutation leases, then waits at most 120 seconds
for existing leases. At 90 seconds it sends cooperative cancellation. Before
the final rehash, cut-over retains an exclusive Windows handle (`CreateFileW`
with required read/write/delete access and `dwShareMode = 0`) for every
existing file in the declared legacy JSON write scope and verifies each
handle's volume/file identity against admission. It hashes through the retained
handle, never by reopening a path. Rename alone is not custody: a handle opened
with `FILE_SHARE_DELETE` can remain valid and write the renamed object. Cut-over
also fences creation or replacement of admission-time-absent paths and
temp-file/rename replacements. If object exclusivity and namespace custody
cannot both be proven until marker commit or abort, it aborts with
`WRITE_SCOPE_NOT_QUIESCED`. This is not approximated by shortening the
rehash-to-commit interval: a legacy writer may have passed its epoch check
before its lease was aborted and otherwise write after the hash. At the
120-second cutoff:

- a lease may be safely aborted only when its operation resolves through the
  ratified static action classification (`hub-actions-v1.csv` together with
  `action-fixture-policy-v1.csv`) to config-only and pre-effect, and it also
  has a live process-birth match and unchanged admission hashes. A lease's
  self-declared class is insufficient. An absent, ambiguous, or digest-mismatched
  classification is `INCOMPLETE_SAFE`;
- a dead/expired process may be force-aborted only after the same checks;
- any lease capable of an external effect without an explicit durable terminal
  receipt is `INCOMPLETE_SAFE`, even if no provider evidence is visible;
- any unknown identity, lock owner, or write-scope hash change is
  `INCOMPLETE_SAFE`.

`INCOMPLETE_SAFE` blocks cut-over and requires evidence-based reconciliation.
It is never converted to success by timeout, process exit, or a missing log.

## 5. Atomic cut-over and recovery

Only after the bounded drain, exclusive write-scope custody, fresh rehash, and
lease classification succeed, one durable transaction writes the new
`authority_epoch`, phase, workspace/home binding, frozen input digests,
classification-table digest, legacy backup/staging reference, and transition
receipt. The transaction re-reads the persisted phase and epoch and commits
only if both equal the values observed at lease admission and the new epoch is
the immediate monotonic successor. A mismatch aborts with
`CUTOVER_EPOCH_CONTENDED` and writes no marker; the persisted epoch is unique
and at most one marker exists per epoch. The authority marker is visible only
after this transaction commits. PeerHub then becomes the sole writer; legacy
files are read-only compatibility inputs.

Recovery is receipt-driven:

- pre-marker failure leaves Engram authoritative and preserves the attempt;
- post-marker failure restores only through the durable transition receipt and
  its referenced verified backup/staging data, never a best-effort replay;
- a command with a possible external effect and no explicit terminal receipt
  remains `INCOMPLETE_SAFE` and is reconciled against provider evidence;
- a previously completed idempotent command returns its stored receipt rather
  than executing a second effect.

Rollback is allowed only while `RETIRED` has not been ratified and only through
the inverse fenced transition, with the same drain, hash, identity, backup,
and receipt requirements. The forward transition receipt records a PeerHub
commit watermark. Rollback enumerates every PeerHub-era mutation after that
watermark whose fact is in the restore scope; if any exists, it refuses with
`PEERHUB_ERA_WRITES_PRESENT` until an audited export and reconciliation
procedure handles those mutations. Restoring a pre-cut-over backup over a fact
PeerHub has since written is prohibited.

## 6. Required Phase 0 proof matrix

No package code starts until deterministic tests/fixtures specify at least:

| Area | Required proof |
|---|---|
| Filesystem | NTFS success; exFAT/FAT/network rejection before mutation; WAL/lock capability failures; NTFS-reporting redirected/virtualized rejection; aliases of one physical home resolve to one identity and lock |
| Identity | resolved volume/file-ID mismatch, copied home/clone, relocation import, home-ID collision |
| Shadow | same-revision equivalence, source/config drift resets, no PeerHub/legacy/provider effect |
| Fence | stale writer commits before/after marker, epoch re-check at final write, concurrent contenders, contended marker CAS rejection, migration-lock renewal loss |
| JSON crash | write-before-receipt crash, changed declared file, omitted write-scope entry rejection, post-safe-abort write attempt under retained-handle custody, already-open `FILE_SHARE_DELETE` handle, admission-time-absent path creation/replacement, custody unobtainable |
| External effect | explicit terminal receipt, absent receipt, ambiguous provider observation, no blind replay |
| Recovery | failure before/after marker, receipt corruption, backup digest mismatch, fenced rollback, rollback refusal after PeerHub-era writes |
| Drain | normal completion, 90-second cancellation, 120-second cutoff, PID reuse/process-birth mismatch, static classification resolution and classification-digest mismatch |
| Quota | missing/stale account evidence, independent workspaces, manual dispatch separate from routing advice |

All proofs bind to fixture IDs, baseline/input digests, stable error codes, and
observable no-effect assertions. A passing happy-path migration alone is not
evidence of authority safety.

## 7. Ratification gate

The final version of this document must carry its SHA-256 and an R:10 decision
record signed by the frozen eligible electorate. The decision must explicitly
approve the supported filesystem boundary, workspace identity, no-dual-writer
rule, hash-checked drain, external-effect handling, and rollback limit. Until
then it is a design input only and does not permit implementation or migration.
