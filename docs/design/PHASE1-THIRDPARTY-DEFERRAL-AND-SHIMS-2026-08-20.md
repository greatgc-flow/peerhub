# Phase 1: Third-Party Deferral Decision and Shim Lifecycle

## 1. Third-Party Adapter Discovery Deferral Decision

**Decision:** The Phase 1 manifest schema design stays strictly within the existing deferral outlined in `ARCHITECTURE.md` section 16.3. No supersession is required.

**Reasoning:**
Section 16.3 explicitly defers "Third-party adapter discovery/signing... wait for a real third-party adapter." As defined in `PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md`, the declarative manifest model strictly limits execution to binding and configuring **reviewed, built-in adapter engines** (the `builtin:*` decoders). It does not provide a mechanism to load, execute, or admit arbitrary third-party Python adapter code or Turing-complete logic into the PeerHub sidecar process. While the manifest can configure the invocation of arbitrary downstream executable binaries (e.g., target CLIs), the actual adapter parsing and protocol bridging is entirely handled by the allowlisted, first-party `decoder.engine` implementations. Therefore, it does not activate genuinely third-party adapter code admission, and the sidecar mechanism remains safely bounded to built-in decoders.

## 2. Compatibility-Shim Admission and Lifecycle Model

To safely manage optional `PATH` shims (e.g., generating a `cc.bat` that proxies to PeerHub) without destabilizing the host environment, the following lifecycle model is enforced:

### 2.1. Triggering Shim Creation
- **Explicit Initiation Only:** Shims are **never** created automatically as a side-effect of adapter discovery or manifest admission. Shim creation is always an explicit, user-initiated or admin-initiated action (e.g., via a CLI command like `peerhub shim add cc`).

### 2.2. Ownership and State Tracking
- **State Registry:** PeerHub maintains a central state registry (e.g., `shim_registry.json`) in its private data directory. This registry records:
  - The absolute path of the generated shim file.
  - The SHA-256 hash of the shim file exactly as PeerHub generated it.
  - The target adapter/profile the shim proxies to.
- **File Signatures:** The shim file itself will contain a distinctive header or comment block (e.g., `:: PeerHub Managed Shim`) to visually indicate its origin to human administrators.
- **PATH Overshadowing (Non-Destructive):** Instead of overwriting existing executables in place, shims should ideally be created in a dedicated `~/.peerhub/bin` directory placed at the front of the user's `PATH`. This overshadows existing tools without destroying their original binaries.

### 2.3. Safe Updates
- **Validation Before Update:** When a shim's underlying binding changes (e.g., updating arguments or target adapter), PeerHub will first read the existing shim file at the recorded path.
- **Hash Verification:** It computes the SHA-256 hash of the existing file and compares it against the expected hash in `shim_registry.json`.
- **Execution:** If the hashes match, PeerHub safely overwrites the shim with the updated definition and updates the registry. If they do not match, the update is safely aborted with a conflict warning to the user.

### 2.4. Safe Removal (Preserving Muscle Memory)
- **Removal Process:** When explicitly removing a shim, PeerHub verifies the file hash. If it matches, the file is deleted, and the registry entry is removed.
- **Muscle-Memory Fallback:** Because shims rely on PATH overshadowing (being placed in a high-priority `~/.peerhub/bin` directory), removing the shim simply causes the OS to fall back to the original executable (e.g., the real `cc.bat`) located elsewhere on the `PATH`. The user's muscle-memory command continues to function, routing directly to the original tool instead of proxying through PeerHub.

### 2.5. Handling External Overwrites
- **External Modification Detection:** If a user or another program overwrites or modifies the shim file outside of PeerHub's knowledge, the file's hash will no longer match the value recorded in `shim_registry.json`.
- **Failsafe Behavior:** In this scenario, PeerHub treats the file as unowned/alien. It will strictly refuse to update or remove the file, logging a warning that the shim path has been co-opted. The user must manually intervene (e.g., via a `--force` flag) to reclaim or clean up the path.
