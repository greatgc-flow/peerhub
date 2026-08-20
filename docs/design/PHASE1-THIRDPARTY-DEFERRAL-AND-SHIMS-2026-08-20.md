# Phase 1: Third-Party Deferral Decision and Shim Lifecycle

## 1. Third-Party Adapter Discovery Deferral Decision

**Decision:** The Phase 1 manifest schema design stays strictly within the existing deferral outlined in `ARCHITECTURE.md` section 16.3. No supersession is required.

**Reasoning:**
Section 16.3 explicitly defers "Third-party adapter discovery/signing... wait for a real third-party adapter." As defined in `PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md`, the declarative manifest model strictly limits execution to binding and configuring **reviewed, built-in adapter engines** (the `builtin:*` decoders). It does not provide a mechanism to load, execute, or admit arbitrary third-party Python adapter code or Turing-complete logic into the PeerHub sidecar process. While the manifest can configure the invocation of arbitrary downstream executable binaries (e.g., target CLIs), the actual adapter parsing and protocol bridging is entirely handled by the allowlisted, first-party `decoder.engine` implementations. Therefore, it does not activate genuinely third-party adapter code admission, and the sidecar mechanism remains safely bounded to built-in decoders.

## 2. Compatibility-Shim Admission and Lifecycle Model

To safely manage optional `PATH` shims (e.g., generating a `cc.bat` that proxies to PeerHub) without destabilizing the host environment, the following lifecycle model is enforced:

### 2.1. Triggering Shim Creation
- **Explicit Initiation Only:** Shims are **never** created automatically as a side-effect of adapter discovery or manifest admission. Shim creation is always an explicit, user-initiated or admin-initiated action (e.g., via a CLI command like `peerhub shim add cc`).

### 2.2. Ownership, State Tracking, and Binding
- **State Registry:** PeerHub maintains a central state registry (e.g., `shim_registry.json`) in its private data directory. This registry records:
  - The absolute path of the generated shim file.
  - The SHA-256 hash of the shim file exactly as PeerHub generated it.
  - The target adapter/profile the shim proxies to.
- **Canonical Target and Admission-Receipt Binding:** A shim's registry entry must tie back to a specific **admission receipt** (from the manifest admission protocol). This binding ensures that a shim can never point at an arbitrary binary on disk; it is strictly bound to an executable that was formally admitted through the manifest system.
- **File Signatures:** The shim file itself will contain a distinctive header or comment block (e.g., `:: PeerHub Managed Shim`) to visually indicate its origin to human administrators.

### 2.3. Initial-Path Collision Handling
- **Fail Closed by Default:** When a user attempts to create a shim, if an existing executable or file already resides at the exact target location before PeerHub touches it, the creation must **fail closed by default**.
- **Evidence Preservation:** Before any overwriting action is taken, PeerHub must record evidence about the pre-existing file, specifically its SHA-256 hash and modification time, for auditing and diagnostics.
- **Explicit Override:** To proceed and overwrite the pre-existing file, the user must provide an explicit override flag (e.g., `--force`).

### 2.4. Safe and Atomic Updates
- **Atomic and Locked Registry Mutation:** All mutations to the `shim_registry.json` file must use an **atomic candidate-snapshot pattern** (consistent with the manifest admission model). A file lock is acquired during the read-modify-write cycle, the registry is written to a temporary snapshot file, and finally moved over the original via an OS-level atomic rename. This guarantees concurrent shim operations cannot corrupt or race the registry.
- **Validation Before Update:** When updating a shim's bindings, PeerHub computes the SHA-256 hash of the existing shim file on disk and compares it against the expected hash in `shim_registry.json`.
- **Execution:** If hashes match, PeerHub safely overwrites the shim and atomically updates the registry. If they mismatch, the update is safely aborted with a conflict warning.

### 2.5. Quoting and Injection Rules
- **Safe Embedding:** Because shims are executable scripts, paths and arguments must be safely embedded in the generated shim content. 
- **Protection Against Special Characters:** Similar to the real path-quoting bug previously found and fixed in `hub.py`'s `cc` dispatch path handling, paths containing spaces or special shell characters (such as the ampersand `&` in the real portable root path) must not cause the shim to misbehave or become exploitable. Strict literal bounding, double-quoting of arguments, and proper escaping must be enforced in the generated shim templates.

### 2.6. PATH Mutation Ownership and Rollback
- **Isolated Directory:** PeerHub is strictly prohibited from scattering shim files into arbitrary existing `PATH` directories. Shims live exclusively in a single PeerHub-owned directory (e.g., `~/.peerhub/shims`).
- **Singular PATH Addition:** This dedicated directory is added to the user's `PATH` environment variable exactly once during setup.
- **Clean Rollback:** To cleanly roll back, PeerHub only needs to remove this single directory entry from the user's `PATH` environment variable, avoiding widespread registry or environment pollution.

### 2.7. Safe Removal (Preserving Muscle Memory)
- **Removal Process:** When explicitly removing a shim, PeerHub verifies the file hash. If it matches, the file is deleted, and the registry entry is atomically removed.
- **Guaranteed Fallback Precondition:** Before shim removal is allowed to proceed, PeerHub must verify that removing the shim will not leave the user stranded. It must verify the existence of the underlying tool elsewhere on the original `PATH`. If no fallback tool is found, removal fails safely, alerting the user.
- **Muscle-Memory Fallback:** With the shim removed from the prepended PeerHub directory, the OS naturally falls back to the original executable, allowing the user's muscle-memory command to continue functioning seamlessly.

### 2.8. Handling External Overwrites and Force-Override Recovery
- **External Modification Detection:** If a file's hash no longer matches `shim_registry.json`, PeerHub treats the file as unowned/alien and refuses to update or remove it by default.
- **Force-Override Recovery Semantics:** The `--force` flag allows users to override fail-closed states (like collision blocks or external overwrites). However, `--force` reduces friction but does not remove every safeguard: even under a force override, PeerHub still enforces atomic registry mutations, still logs the pre-existing file's hash/mtime on collisions, and still strictly requires a valid admission receipt to generate the new shim. The overriding action must safely record its state without violating the registry's transactional integrity.
