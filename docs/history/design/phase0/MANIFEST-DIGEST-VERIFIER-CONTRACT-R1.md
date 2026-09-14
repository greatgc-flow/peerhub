# Manifest Digest Verifier Contract R1

Status: proposed host-tooling contract. It governs verification of ratification
claims only; it is not PeerHub package code and grants no implementation,
consensus, Hub-state, provider, broker, or cutover authority.

## Scope and host boundary

The verifier is read-only with respect to every verified artifact. It resolves
every manifest path beneath the declared workspace root, rejects absolute
paths, `.`/`..`, alternate data streams, symlinks, and reparse-point traversal,
then reads a stable snapshot before calculating a claim. It may emit a receipt
only to a host-configured, create-only path beneath a designated scratch root.
It rejects an existing, tracked, non-scratch, escaping, or reparse-point receipt
path. It cannot finalize a round, modify checked files, write Hub state, or
invoke a provider.

The host—not a manifest field—runs `git status --porcelain`. Any tracked-file
change is `WORKING_TREE_DIRTY` with retry `CONDITIONAL`. Current untracked
Phase 0 proposal files are an adoption exception: they may be bound by exact
byte hash, but a finalized future artifact must cite both a commit and its
raw-byte hash after the initial adoption commit.

## Descriptor

Each proposal supplies a detached descriptor with a proposal path/hash, zero
or more dependency claims, and optional measured claims. The descriptor cannot
list itself as a dependency.

| Field | Rule |
|---|---|
| path | NFC UTF-8, `/` separator, workspace-relative; reject backslashes, drive/drive-relative forms, UNC/device prefixes, traversal, ADS, reparse points, symlinks, and non-NFC input before OS resolution |
| transform | `RAW_BYTES` or `CANONICAL_TEXT_VECTOR` only |
| digest | lowercase 64-hex SHA-256 |
| role | `normative`, `evidence_only`, or `snapshot` |
| measured claim | explicit count, set-equality, JSON field, or named vector digest |

`RAW_BYTES` hashes the exact bytes. `CANONICAL_TEXT_VECTOR` is allowed only
for an explicit ordered line vector, never as a replacement for a document's
raw hash: UTF-8 text, LF separators, one trailing LF, and the exact selector
named by the claim. No whitespace stripping or implicit file-type conversion
is permitted. Every `normative` dependency MUST include a `RAW_BYTES` claim;
`CANONICAL_TEXT_VECTOR` may only be an additional measured or evidence-only
claim.

## Immutable history and snapshots

Previously ratified proposal/decision paths are immutable at their recorded
raw hash. The verifier obtains those path/hash pairs only from its
host-anchored, hash-bound provenance registry (initially
`RATIFICATION-PROVENANCE-INDEX-R1.md`); an unratified descriptor cannot define
or replace a historical baseline. A changed historical path is
`HISTORICAL_REWRITE_REJECTED`; a new prospective file and round are required.
A `snapshot` dependency is copied verbatim before verification; the
archive-copy hash, not a mutable live Hub record, is what a new round may bind.
A byte/identity change during snapshot read is `SNAPSHOT_CONSISTENCY_FAILED`.

## Stable failures

| Code | Certainty | Retry |
|---|---|---|
| `PATH_OUT_OF_SCOPE` | `NOT_STARTED` | `NEVER` |
| `UNSAFE_REPARSE_POINT` | `NOT_STARTED` | `NEVER` |
| `RECEIPT_PATH_REJECTED` | `NOT_STARTED` | `NEVER` |
| `HISTORICAL_BASELINE_UNAVAILABLE` | `NOT_STARTED` | `CONDITIONAL` |
| `MANIFEST_HASH_MISMATCH` | `NOT_STARTED` | `NEVER` |
| `VECTOR_TRANSFORM_MISMATCH` | `NOT_STARTED` | `NEVER` |
| `WORKING_TREE_DIRTY` | `NOT_STARTED` | `CONDITIONAL` |
| `HISTORICAL_REWRITE_REJECTED` | `NOT_STARTED` | `NEVER` |
| `SNAPSHOT_CONSISTENCY_FAILED` | `NOT_STARTED` | `SAFE` |
| `UNTRACKED_PATH_DISALLOWED` | `NOT_STARTED` | `CONDITIONAL` |

## Acceptance evidence

The first verifier implementation must retain one red/green run for each:

1. R11's transcribed crosswalk digest fails `MANIFEST_HASH_MISMATCH` while the
   measured digest passes.
2. The no-trailing-LF action vector fails `VECTOR_TRANSFORM_MISMATCH`; the
   canonical parser-order, LF-trailing digest `2065c0b6de16cc39224bd3d364199383c2f625c1a6564e642fc853b76d76196d` passes.
3. A tracked-file edit fails `WORKING_TREE_DIRTY`; a historical-path hash
   change fails `HISTORICAL_REWRITE_REJECTED`.
4. A normative dependency with only a text-vector claim fails before digest
   comparison; a descriptor-supplied historical digest cannot replace the
   host-anchored baseline; an existing or tracked receipt destination fails
   `RECEIPT_PATH_REJECTED`.

The red fixtures are isolated copies. No test modifies a historical file or
live Hub record.
