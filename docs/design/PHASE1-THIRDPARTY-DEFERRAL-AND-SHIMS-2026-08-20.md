# Phase 1: Third-Party Deferral Decision and Shim Lifecycle

## 1. Third-Party Adapter Discovery Deferral Decision

**Decision:** The Phase 1 manifest schema design stays strictly within the existing deferral outlined in `ARCHITECTURE.md` section 16.3. No supersession is required.

**Reasoning:**
Section 16.3 explicitly defers "Third-party adapter discovery/signing... wait for a real third-party adapter." As defined in `PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md` and `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`, the declarative manifest model strictly limits execution to binding and configuring **reviewed, built-in adapter engines** (the `builtin:*` decoders). It does not provide a mechanism to load, execute, or admit arbitrary third-party Python adapter code or Turing-complete logic into the PeerHub sidecar process. While the manifest can configure the invocation of arbitrary downstream executable binaries (e.g., target CLIs), the actual adapter parsing and protocol bridging is entirely handled by the allowlisted, first-party `decoder.engine` implementations. Therefore, it does not activate genuinely third-party adapter code admission, and the sidecar mechanism remains safely bounded to built-in decoders.

## 2. Compatibility-Shim Admission and Lifecycle Model

To safely manage optional `PATH` shims (e.g., generating a `cc.bat` that proxies to PeerHub) without destabilizing the host environment, the following lifecycle model is enforced:

### 2.1. Triggering Shim Creation
- **Explicit Initiation Only:** Shims are **never** created automatically as a side-effect of adapter discovery or manifest admission. Shim creation is always an explicit, user-initiated or admin-initiated action (e.g., via a CLI command like `peerhub shim add cc`).

### 2.2. Ownership, State Tracking, and Binding
- **State Registry:** PeerHub maintains a central state registry (`shim_registry.json`) in its private data directory (`~/.peerhub/shim_registry.json` or `%LOCALAPPDATA%\peerhub\shim_registry.json`). This registry records:
  - `shim_name`: Canonical identifier (e.g. `cc`).
  - `target_path`: Absolute path of the generated shim file.
  - `sha256`: SHA-256 hash of the shim file exactly as PeerHub generated it.
  - `target_adapter`: Profile/adapter target the shim proxies to.
  - `admission_receipt_id`: The unified, real admission receipt ID (e.g., `receipt-cc-claude-peer-20260820T215000Z-a1b2c3d4`) from the corresponding Phase 1 AdmissionRegistry.
  - `backups`: Array of historical pre-existing file backup records.
- **Canonical Target and Admission-Receipt Binding:** A shim's registry entry must tie back to a specific **admission receipt** (from the unified manifest and executable admission protocol). This binding ensures that a shim can never point at an arbitrary binary on disk; it is strictly bound to an executable that was formally admitted with full executable integrity validation (hash chains and ACLs).
- **File Signatures:** The shim file itself will contain a distinctive header comment block (`:: PeerHub Managed Shim - DO NOT EDIT MANUALLY`) to visually indicate its origin to human administrators.

### 2.3. Initial-Path Collision Handling and Pre-Overwrite Backup
- **Fail Closed by Default:** When a user attempts to create a shim, if an existing executable or file already resides at the exact target location before PeerHub touches it, the creation must **fail closed by default** (`ERR_SHIM_COLLISION_DETECTED`).
- **Evidence Preservation & State Recording:** Before any overwriting action is taken, PeerHub records forensic evidence about the pre-existing file:
  - Exact file size in bytes.
  - SHA-256 hash of the existing file contents.
  - Original file modification timestamp (`mtime`) and filesystem permission bits.
- **Explicit Override via `--force`:** To proceed and overwrite a colliding pre-existing file, the user must provide an explicit `--force` flag.
- **Mandatory Pre-Overwrite Physical Backup:** When `--force` is provided, PeerHub **must create a recoverable byte-for-byte backup copy** of the pre-existing file prior to modifying or overwriting the destination. Recording metadata alone is insufficient.

### 2.4. Safe and Atomic Updates
- **Atomic and Locked Registry Mutation:** All mutations to the `shim_registry.json` file must use an **atomic candidate-snapshot pattern** (consistent with the manifest admission model). A file lock is acquired during the read-modify-write cycle, the registry is written to a temporary snapshot file (`shim_registry.json.tmp.<pid>`), and finally moved over the original via an OS-level atomic rename (`os.replace` / `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)`). This guarantees concurrent shim operations cannot corrupt or race the registry.
- **Validation Before Update:** When updating a shim's bindings, PeerHub computes the SHA-256 hash of the existing shim file on disk and compares it against the expected hash in `shim_registry.json`.
- **Execution:** If hashes match, PeerHub safely overwrites the shim and atomically updates the registry. If they mismatch, the update is safely aborted with a conflict warning (`ERR_SHIM_EXTERNALLY_MODIFIED`).

### 2.5. Concrete Argument-Serialization and Quoting Rules for Windows `cmd.exe`

Because generated Windows batch scripts (`.bat` / `.cmd`) are evaluated by `cmd.exe` before invoking downstream runtimes, naive string concatenation exposes execution to path corruption (e.g. paths containing spaces, parentheses, or the ampersand `&` character present in portable installations such as `D:\Engram&Peerhub\PortableDev (v2.1)`) and command injection.

To eliminate these vulnerabilities, PeerHub implements a deterministic, character-by-character argument serialization algorithm based on MSVCRT `CommandLineToArgvW` rules and `cmd.exe` escape semantics:

#### 2.5.1. Character-by-Character Serialization Algorithm
When generating batch scripts or passing command-line arguments to `cmd.exe`, every embedded argument is transformed using the following exact rules:

```python
def serialize_cmd_argument(arg: str) -> str:
    """
    Serializes an arbitrary string argument into a safely quoted token for Windows
    cmd.exe batch script embedding and MSVCRT CommandLineToArgvW consumption.
    
    Escape Semantics:
    1. cmd.exe Percent Escaping:
       In batch files, '%' is parsed as variable syntax (e.g. %VAR%, %1, %~dp0).
       Every literal '%' must be doubled as '%%' regardless of whether it appears
       inside or outside double quotes.
       
    2. Enclosure Gate:
       If the argument is empty or contains any of the delimiter/metacharacters:
         Whitespace: ' ' (space), '\t' (tab), '\n', '\v', '\f', '\r'
         Cmd Separators: ',', ';', '='
         Cmd Metacharacters: '&', '|', '(', ')', '<', '>', '^'
         Variable Markers: '%', '!'
         Quotes: '"'
       Then the argument MUST be wrapped in surrounding double quotes ("...").
       
    3. MSVCRT Backslash and Double-Quote Escaping:
       - Consecutive backslashes (\) followed immediately by a double quote (")
         must be doubled: N backslashes + " becomes (2N + 1) backslashes + ".
       - Interior double quotes (") become '\"' (preceded by doubled backslashes).
       - Trailing backslashes before the closing delimiter quote must be doubled:
         N trailing backslashes become 2N backslashes, ensuring the closing quote
         is parsed as a delimiter rather than an escaped literal.
    """
    if arg == "":
        return '""'

    # Step 1: Double percent characters for cmd.exe batch evaluation
    escaped = arg.replace('%', '%%')

    # Step 2: Check if quoting delimiter is required
    DELIMITERS = set(' \t\n\v\f\r,;=&|()<>^!"')
    needs_quotes = any(c in DELIMITERS for c in escaped)

    if not needs_quotes:
        return escaped

    # Step 3: Character-by-character MSVCRT escaping
    result = []
    bs_count = 0
    for char in escaped:
        if char == '\\':
            bs_count += 1
        elif char == '"':
            # Double preceding backslashes and escape the quote
            result.append('\\' * (2 * bs_count + 1))
            result.append('"')
            bs_count = 0
        else:
            if bs_count > 0:
                result.append('\\' * bs_count)
                bs_count = 0
            result.append(char)

    # Step 4: Double trailing backslashes before closing quote
    if bs_count > 0:
        result.append('\\' * (2 * bs_count))

    return '"' + ''.join(result) + '"'
```

#### 2.5.2. Standard Shim Script Generation Template & Clean Two-Layer Quoting Separation
Generated Windows batch shims maintain a strict separation between **batch-level variable assignment quoting** and **command-invocation quoting** to prevent syntax errors on paths containing spaces and ampersands (e.g. `D:\Engram&Peerhub\PortableDev (v2.1)`):

- **Layer 1 (Batch Assignment: `set "PEERHUB_EXE=..."`)**: The assignment uses outer quotes `set "VAR=VAL"` to safely protect delimiter and control characters (`&`, `(`, `)`, spaces) during batch script evaluation, while storing the clean **unquoted** path in the variable. `serialize_cmd_argument()` (which adds argv-level enclosing quotes) must NOT be used inside `set "VAR=VAL"`, as doing so would create nested `""` that prematurely closes quotes and splits commands on `&`.
- **Layer 2 (Command Invocation: `"%PEERHUB_EXE%"`)**: When invoking the target binary, `%PEERHUB_EXE%` is wrapped in exactly one pair of double quotes on the execution line. This ensures `cmd.exe` passes the complete, uncorrupted executable path to the OS loader.
- **`@echo off` & `setlocal`**: Suppresses command echo and disables delayed expansion to prevent exclamation marks `!` from corrupting argument strings.
- **`%*` Argument Propagation**: Forward caller arguments directly to the invocation line.

**Canonical Generated `.bat` Shim Template:**
```cmd
@echo off
:: PeerHub Managed Shim - DO NOT EDIT MANUALLY
:: Shim ID: {shim_name} | Admission Receipt: {admission_receipt_id}
setlocal EnableExtensions DisableDelayedExpansion
set "PEERHUB_EXE={unquoted_target_exe_path}"
"%PEERHUB_EXE%" run --profile "{profile_name}" %*
exit /b %ERRORLEVEL%
```

### 2.6. PATH Mutation Ownership and Rollback
- **Isolated Directory:** PeerHub is strictly prohibited from scattering shim files into arbitrary existing `PATH` directories. Shims live exclusively in a single PeerHub-owned directory (e.g., `~/.peerhub/shims` or `%LOCALAPPDATA%\peerhub\shims`).
- **Singular PATH Addition:** This dedicated directory is added to the user's `PATH` environment variable exactly once during setup.
- **Clean Rollback:** To cleanly roll back, PeerHub only needs to remove this single directory entry from the user's `PATH` environment variable, avoiding widespread registry or environment pollution.

### 2.7. Safe Removal (Preserving Muscle Memory)
- **Removal Process:** When explicitly removing a shim (`peerhub shim remove <shim_name>`), PeerHub verifies the file hash against `shim_registry.json`. If it matches, the file is deleted, and the registry entry is atomically removed.
- **Guaranteed Fallback Precondition:** Before shim removal is allowed to proceed, PeerHub must verify that removing the shim will not leave the user stranded. It must verify the existence of the underlying tool elsewhere on the original `PATH` (outside the PeerHub shim directory). If no fallback tool is found, removal fails safely with a warning, alerting the user.
- **Muscle-Memory Fallback:** With the shim removed from the prepended PeerHub directory, the OS naturally falls back to the original executable, allowing the user's muscle-memory command to continue functioning seamlessly.

### 2.8. Force-Override Collision Path: Backup Persistence and Concrete Rollback Protocol

When `--force` is supplied to override a pre-existing unowned file or resolve an external collision, PeerHub implements a complete, recoverable backup lifecycle:

#### 2.8.1. Dedicated Backup Storage Structure
Backups are persisted in an isolated, immutable archive directory under the PeerHub data root:
```
~/.peerhub/backups/shims/<shim_name>/
  ├── <timestamp_iso>_<sha256_short>.bak       # Exact byte copy of pre-existing file
  └── backup_meta.json                         # Forensic backup manifest
```

#### 2.8.2. Concrete Pre-Overwrite Backup Algorithm
When overwriting an existing collision target `P`:
1. **Acquire Registry Lock:** Acquire process-exclusive lock on `shim_registry.json.lock`.
2. **Compute Collision Digest:** Calculate `sha256(P)`, read file size, permissions, and `mtime`.
3. **Stage Backup Copy:**
   - Create directory `~/.peerhub/backups/shims/<shim_name>/`.
   - Copy file `P` to temporary file `~/.peerhub/backups/shims/<shim_name>/<timestamp>.bak.tmp`.
   - Verify `sha256(backup_tmp) == sha256(P)`. If verification fails, immediately abort with `ERR_BACKUP_VERIFICATION_FAILED` without modifying `P`.
   - Atomically rename `.bak.tmp` to finalized backup filename `<timestamp_iso>_<sha256[:12]>.bak`.
4. **Persist Backup Metadata:**
   Write / update `backup_meta.json` with schema:
   ```json
   {
     "shim_name": "cc",
     "target_path": "C:\\Users\\User\\.peerhub\\shims\\cc.bat",
     "backup_file": "C:\\Users\\User\\.peerhub\\backups\\shims\\cc\\20260820T143000Z_a1b2c3d4e5f6.bak",
     "original_sha256": "a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890",
     "original_mtime_epoch": 1787234567.89,
     "original_file_size_bytes": 1042,
     "original_permissions_octal": "0o755",
     "backup_created_at": "2026-08-20T14:30:00Z",
     "override_reason": "FORCE_COLLISION_OVERWRITE",
     "restored": false
   }
   ```
5. **Update State Registry:** Atomically update `shim_registry.json` to append the backup metadata entry to `installed_shims[shim_name].backups`.
6. **Atomic Shim Overwrite:** Only after the backup copy and registry entry are securely synced to disk, atomically write the new shim to target path `P`.

#### 2.8.3. Concrete Restoration Protocol (`peerhub shim restore <shim_name>`)
If a user wishes to undo an override or restore the original file:
1. **Lookup Backup Record:** Read `shim_registry.json` and locate the most recent unrestored backup record for `<shim_name>`.
2. **Verify Backup Integrity:** Confirm the backup file exists on disk and compute its SHA-256 hash. Verify `sha256(backup_file) == original_sha256`. If mismatched, abort with `ERR_CORRUPT_BACKUP_ARCHIVE`.
3. **Atomic File Restoration:**
   - Copy backup file to staging location `P.restoring.<pid>`.
   - Restore original `mtime` timestamp and filesystem permission bits to the staged file.
   - Atomically rename `P.restoring.<pid>` over `P` (`os.replace`), replacing the PeerHub shim with the original file.
4. **Registry Cleanup:**
   - Mark the backup record as `"restored": true` and record `"restored_at": "<timestamp_iso>"`.
   - Remove the active shim entry from `shim_registry.json`.
   - Release registry lock and log confirmation to operator.
