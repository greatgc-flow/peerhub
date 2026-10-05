# Artifact Store Contract (M2.1)

## 1. Authority & Storage Layout
- **Authority:** Artifact references committed to the Core Event Stream dictate the lifecycle of artifacts. Physical files with no pointing Core reference are ORPHANS subject to garbage collection (ART-028).
- **Storage Layout:**
  - `artifacts/`: Flat directory storing files named strictly by their SHA-256 lowercase hex digest.
  - `artifacts/.tmp/`: Directory for STAGED/DIGESTED files during upload (via `mkstemp`).

## 2. Port Protocols & Error Types
- `stage(stream) -> StagedArtifact`: Writes chunked bytes to `.tmp`. Errors: `IOError_ENOSPC`, `MemoryError`, `PartialReadError`.
- `commit(StagedArtifact) -> DigestRef`: Atomically moves file to final path. Errors: `FileExistsError` (handled idempotently), `ForbiddenTransitionError`.
- `read(DigestRef) -> stream`: Opens committed file for reading. Errors: `ArtifactNotFoundError`, `ArtifactTamperedError`.
- `resolve(string) -> DigestRef`: Validates digest format. Errors: `InvalidArtifactReferenceError`, `SecurityBoundaryError`.

## 3. Ordering & Idempotency Rules
1. **Stage** stream fully to `.tmp` file (64KB chunks to prevent OOM).
2. **Digest** simultaneously to compute SHA-256.
3. **Verify** limits (e.g. disk capacity constraints).
4. **Atomic Blob Commit** via `os.rename` to `artifacts/<hash>`.
5. **Metadata Write** to ArtifactStore index DB.
6. **Core Record Reference** appended.
- **Idempotency:** Uploading identical payloads yields the same hash; the final atomic rename catches `FileExistsError` and safely returns the existing reference, averting redundant IO (ART-016, ART-017).

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
