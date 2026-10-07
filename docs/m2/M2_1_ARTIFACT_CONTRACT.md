# Artifact Store Contract (M2.1)

## 1. Authority & Storage Layout
- **Authority:** Artifact references committed to the Core Event Stream dictate the lifecycle of artifacts. Physical files with no pointing Core reference are ORPHANS subject to garbage collection (ART-028).
- **Storage Layout:**
  - `artifacts/<digest[:2]>/<digest>`: 2-tier sharded directory hierarchy using the first 2 characters of the lowercase SHA-256 hex digest to circumvent OS flat-directory file limits (Decision #5).
  - `artifacts/.tmp/`: Directory for STAGED/DIGESTED files during upload (via `mkstemp`).
  - **Garbage Collection (GC):** Staged `.tmp` files older than 24 hours (stranded by process crashes mid-stage) are swept cleanly upon Host boot (Decision #2, ART-030).

## 2. Port Protocols & Error Types
- `stage(stream) -> StagedArtifact`: Writes chunked bytes to `.tmp`. Errors: `IOError_ENOSPC`, `MemoryError`, `PartialReadError`.
- `commit(StagedArtifact) -> DigestRef`: Atomically moves file to final path. Errors: `FileExistsError` (handled idempotently), `ForbiddenTransitionError`.
- `read(DigestRef) -> stream`: Opens committed file for reading. Errors: `ArtifactNotFoundError`, `ArtifactTamperedError`.
- `resolve(string) -> DigestRef`: Validates digest format. Errors: `InvalidArtifactReferenceError`, `SecurityBoundaryError`.

## 3. Ordering & Idempotency Rules
1. **Stage** stream fully to `.tmp` file (64KB chunks to prevent OOM).
2. **Digest** simultaneously to compute SHA-256.
3. **Verify** limits (e.g. disk capacity constraints).
4. **Atomic Blob Commit** via `os.rename` to `artifacts/<digest[:2]>/<digest>`.
5. **Metadata Write** to ArtifactStore index DB.
6. **Core Record Reference** appended.
- **Idempotency & Deduplication:** If two peers commit identical payloads independently, the physical disk blob is content-addressed and deduplicated via atomic rename catch of `FileExistsError`. Meanwhile, the Core event stream records strictly maintain each peer's unique provenance record (Decision #3, ART-016, ART-017).
- **Disabled Extension Passthrough:** When the Artifact extension is OFF, the M1 bridge treats `m2.artifact.reference` payload as an opaque map, gracefully degrading to raw view without stream read crash (Decision #6, ART-012).

## 4. Crash Matrix & Recovery
| Crash Point | Resulting State | Recovery Action | Covering Test |
| :--- | :--- | :--- | :--- |
| Disk Full (ENOSPC) | Partial `.tmp` file | Aborts commit, raises explicit error. `.tmp` file aggressively removed. | ART-003 |
| Mid-Rename Crash | `.tmp` file exists | Process dies. OS guarantees rename is atomic. Boot GC clears stranded `.tmp`. | ART-030 |
| Core DB Crash | Blob committed, no Record | Orphan state. Garbage collection sweeps unreferenced blob cleanly. | ART-001 |
| Local DB Lost | Files on disk | `CorruptMetadataError` recovered by rehashing physical files on demand. | ART-035 |

## 5. Security & Trust Boundary
- **Traversals & Symlinks:** Strictly rejected using `os.path.islink` and regex bounds before interacting with the OS file API (ART-005, ART-006).
- **Immutability:** Tampering detected via read-time re-hash and size check (ART-009, ART-010). Files have OS `stat.S_IWRITE` unset upon commit.
- **Trust:** Core strictly relies on cryptographic SHA-256 equality, zero reliance on user-provided filenames.

## 6. Windows-Specific Rules
- **Reserved Names:** Paths resolving to `CON`, `NUL`, `PRN` strictly rejected via pre-OS regex (ART-013).
- **Reparse Points:** Windows Junctions rejected via `FILE_ATTRIBUTE_REPARSE_POINT` (ART-007).
- **Path Lengths:** Process uses Windows `\\?\` absolute path prefix natively to bypass the 260 character `MAX_PATH` limit (ART-014).
- **Collisions:** Filename collisions impossible due to strict ASCII lowercase hex digest enforcement. No case folding or NFC/NFD Unicode normalization risks (ART-008).
- **Concurrent Deletion:** Windows mandatory read-locks prevent GC of open files. Store handles this safely by deferring deletion (ART-033).

## 7. Null/Empty/Unknown Semantics
- **0-Byte Payloads:** Permitted and explicitly hashed as `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (ART-032).
- **Internal NUL Bytes:** `\x00` strictly handled as raw binary block; never causes premature string truncation (ART-031).

## 8. Interaction List
- Extension -> Host: `emit_artifact(stream)`
- Host -> ArtifactStore: `stage()`, `commit()`
- Core -> ArtifactStore: `read(ref)`
- ArtifactStore -> OS: `os.rename()`, `hashlib.sha256()`, `mkstemp()`, `fsync()`

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. State-machine and exception JSON catalogs are updated separately.

Section 1: replace the staging-GC bullet:

```markdown
  - **Garbage Collection (GC):** `sweep_staging(max_age_seconds=86400.0)` explicitly removes old staging files; the ArtifactStore and Host do not automatically invoke this sweep at boot. `cleanup_orphans(referenced_digests, min_age_seconds=0.0)` removes unreferenced committed blobs using the caller-supplied reference set.
```

Section 2: replace the interface list:

```markdown
- `stage_stream(stream, chunk_size=65536, max_size=None) -> StagedArtifact`: Streams bytes into a temporary file while computing SHA-256; fsyncs the staged file. `chunk_size` must be a positive integer. A supplied `max_size` must be a non-negative integer; exceeding it raises `ValueError` before the excess chunk is written, and removes the temporary file.
- `commit_staged(staged) -> str`: Verifies staged content and publishes the digest-addressed blob with `os.replace`. Errors: `ForbiddenTransitionError`, `SecurityBoundaryError`, `ArtifactTamperedError`, and filesystem errors.
- `read_bytes(digest) -> bytes`: Returns the complete blob after digest verification. Errors: `ArtifactNotFoundError`, `ArtifactTamperedError`.
- `read_verified_prefix(digest, limit, chunk_size=65536) -> tuple[bytes, int]`: Streams and verifies the entire blob while retaining only the requested prefix; returns the prefix and total size.
- `resolve_path(digest) -> Path`: Validates the reference and artifact path boundary. Errors: `InvalidArtifactReferenceError`, `SecurityBoundaryError`.
- `export_verified(digest, dest, chunk_size=65536) -> int`: Streams the blob into a sibling temporary file, fsyncs it, verifies its digest, and only then replaces `dest`. Returns the exported byte count. Verification failure removes the temporary file and does not publish the export.
```

Section 3: replace the numbered sequence and deduplication bullet:

```markdown
1. **Stage & Digest** the stream into `.tmp` using bounded chunks and simultaneous SHA-256 calculation.
2. **Bound Check** each chunk before writing it when `max_size` is supplied.
3. **Staging Durability** via file fsync before returning `StagedArtifact`.
4. **Commit Verification** rehashes the staged file and checks its size against `StagedArtifact`.
5. **Atomic Blob Commit** publishes the file with `os.replace` to `artifacts/<digest[:2]>/<digest>`.
6. **Commit-Time Grace** resets the committed blob's modification time; orphan-GC grace is measured from commit rather than the earlier staging write.
7. **Directory Durability** attempts to fsync the shard directory on POSIX, and also the artifact root when a new shard was created. Directory fsync is skipped on Windows and is best-effort when the OS rejects it.
8. **Core Record Reference** is appended separately by the caller after blob commit.
- **Idempotency & Deduplication:** An existing blob is fully verified through streamed `read_verified_prefix(digest, 0)` before redundant staging is discarded. This path does not load the complete existing blob into memory and refreshes its modification time, renewing its orphan-GC grace period.
- **GC Grace:** `min_age_seconds` must be non-negative. An unreferenced blob younger than the supplied grace period is retained. The default is zero; callers running GC beside writers must supply an appropriate grace period.
- **Lifecycle Representation:** Stage, digest, verification, and commit are operational phases, not separately persisted artifact status values. `commit_staged` accepts `StagedArtifact` and performs verification internally.
```

Section 4: replace these rows:

```markdown
| Core DB Crash | Blob committed, no Record | Caller-driven orphan cleanup may remove the blob after any configured commit-time grace period. | test_art_039_gc_commit_grace |
| Duplicate commit | Existing digest-addressed blob | Stream-verify the existing blob, renew its GC grace, and discard redundant staging. | test_art_038_streamed_dedup |
| Export verification failure | Temporary export contains untrusted bytes | Remove the temporary export; do not replace the destination. | test_art_040_verified_export |
```

Delete **Local DB Lost**: this ArtifactStore has no metadata index DB.

Section 6: replace **Path Lengths**:

```markdown
- **Path Lengths:** Artifact paths use `pathlib.Path`; this implementation does not explicitly add the Windows `\\?\` prefix. Long-path support depends on the host environment.
```

Section 8: replace interface names with the implemented names above, and replace `os.rename()` with `os.replace()`.

**Wrong/obsolete:** automatic Host-boot sweeping, metadata-index writes/recovery, `os.rename` as the publication primitive, deduplication without verification IO, unconditional directory durability, and a separately enforced persisted artifact lifecycle.
