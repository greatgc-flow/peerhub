# Phase 0 fixture evidence collection

> Status: active evidence plan. A fixture definition is not a capture. Phase 1
> remains blocked until every required fixture has a digest-bound capture or an
> explicit `BLOCKED_LIVE_CAPTURE` record permitted by the fixture contract.

## Capture classes

| Class | Scope | Allowed effects | Fixture families |
|---|---|---|---|
| `READ_ONLY_BASELINE` | pinned Engram workspace/config | none | CLI/JSONL validation, static health/routing inspection |
| `ISOLATED_STATE` | fresh NTFS temporary AI root | local state only | session, room, consensus, CAS, lease, routing state |
| `CONTROLLED_FAKE` | isolated root plus deterministic local fake adapter | local process/artifact only | pipe, PTY, provider/probe failure, recovery |
| `BLOCKED_LIVE_CAPTURE` | no execution | none | only a live paid/destructive/non-deterministic case with safe substitute |

No capture may use the live `.ai` state for a mutating case, consume a reset
credit, or call a provider merely to obtain a transcript. Every isolated root
must be on local NTFS and destroyed only after its final manifest/receipt is
verified and copied into this directory.

## Required record fields

Each `captures/<fixture-id>.json` contains: fixture ID/domain/class, baseline
revision digests, sanitized execution input, preconditions, redaction policy,
exit/result evidence, transcript and before/after state SHA-256 values, and
its own canonical record SHA-256. A blocker instead names the unsafe live
effect, why it is unsafe, the deterministic substitute fixture, and the later
empirical test required before release.

## Ordering

1. Reverify pinned baseline digests and create a fresh isolated NTFS root.
2. Capture read-only baseline fixtures.
3. Capture isolated state fixtures serially.
4. Capture fake-adapter/process fixtures serially.
5. Create permitted blocker records, validate all digests and the 90-action
   fixture join, then submit the evidence set for a new Phase 0 review.
