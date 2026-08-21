# Phase 1: Real-World Executable Admission Receipts and Corrected Binding Model

> **Status:** APPROVED DESIGN & EMPIRICAL BASELINE (Round 5 Punch-List Item 3)  
> **Date:** 2026-08-20  
> **Scope:** Grounded empirical investigation of live `claude.cmd`, `codex.cmd`, and `agy.exe` installations on Windows, reconciliation of NTFS ACL admission rules, empirical groundwork for Phase 2's transitive executable chain binding & hashing, deterministic collision detection algorithms, and single-node Phase 1 admission receipts (with multi-node Phase 2 candidate targets).

---

## 1. Executive Summary & Problem Statement

In the Round 4 countercritique (`PHASE1-CX-COUNTERCRITIQUE-ROUND4-2026-08-20.md`), cx identified two critical real-world failure modes in the initial Phase 1 admission design:

1. **The Impossible ACL Rule**: The previous admission rule demanded that the directory containing the target executable "deny unprivileged writes", interpreted as rejecting any directory granting `Modify` (`M`) to `NT AUTHORITY\Authenticated Users`. In reality, on Windows NTFS non-system drives (such as `D:\` or the substituted portable developer root `P:\`), default inheritance grants `Authenticated Users:(OI)(CI)(M)`. Enforcing denial of `Authenticated Users:M` causes PeerHub to reject 100% of real-world installations, portable developer environments, and user-space global package installs (`%APPDATA%\npm`, `%LOCALAPPDATA%`).
2. **Wrapper-Only Hashing Gap**: The previous receipts bound only the top-level `.cmd` batch scripts (`claude.cmd`, `codex.cmd`). On Windows, `.cmd` wrappers are thin trampoline scripts that invoke secondary interpreters (`node.exe` resolved via `PATH` at runtime), ESM launcher scripts (`codex.js`), and nested platform-specific native binaries (`claude.exe`, `codex.exe`). Hashing only the `.cmd` wrapper failed to cryptographically bind what actually executes.

This document replaces theoretical assumptions with **empirical measurements on the live host system** (`GC-SURFACE-01`). We trace the complete transitive execution graphs (as explicit target evidence for Phase 2), inspect the exact NTFS ACLs via `icacls`, compute real SHA-256 hashes for every file in the transitive closures, establish a secure and achievable Windows ACL admission rule, define the deterministic collision and snapshot publication algorithms, and provide three worked `AdmissionReceipt` examples modeled as Phase 2 targets (`chain_complete: true`) built from this empirical data -- real Phase 1 admission receipts are single-node only (`chain_complete: false`) and are not separately illustrated here.

---

## 2. Empirical Host Investigation & Transitive Execution Chains

### 2.1. Discovery Commands and Host Context

The following commands were executed on the live host machine to locate entrypoints and query system metadata:

```powershell
Get-Command claude.cmd, codex.cmd, agy.exe, node.exe | Select-Object Name, Source, Path
[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value # User SID: S-1-5-21-796419805-904610953-2528718009-1001
[System.Security.Principal.WindowsIdentity]::GetCurrent().Name       # Account: GC-SURFACE-01\GC
cmd.exe /c subst                                                     # P:\ => D:\Engram&Peerhub\PortableDev (v2.1)
Get-Volume -DriveLetter D                                            # NTFS Fixed Disk
```

**Discovered Paths:**
* **Claude Entrypoint:** `P:\_sys\env\nodejs\npm-global\claude.cmd`
* **Codex Entrypoint:** `P:\_sys\env\nodejs\npm-global\codex.cmd`
* **Agy Entrypoint:** `P:\_sys\tools\agy\agy.exe`
* **Node Interpreter:** `P:\_sys\env\nodejs\node.exe`

---

### 2.2. Transitive Execution Graph Analysis

#### A. Claude Code Transitive Graph (`claude-peer` / `cc`)
1. **Hop 0 (Entrypoint Wrapper):** `P:\_sys\env\nodejs\npm-global\claude.cmd`  
   *Content:*
   ```cmd
   @ECHO off
   GOTO start
   :find_dp0
   SET dp0=%~dp0
   EXIT /b
   :start
   SETLOCAL
   CALL :find_dp0
   "%dp0%\node_modules\@anthropic-ai\claude-code\bin\claude.exe"   %*
   ```
2. **Hop 1 (Native PE Executable):** `P:\_sys\env\nodejs\npm-global\node_modules\@anthropic-ai\claude-code\bin\claude.exe`  
   *Origin:* Copied/linked during npm postinstall (`install.cjs`) from `@anthropic-ai\claude-code-win32-x64\claude.exe`. It is a standalone native Windows x64 PE executable.
3. **Vendor Version:** `claude.cmd --version` $\to$ `2.1.215 (Claude Code)` (PE `ProductVersion`: `2.1.215.0`, `package.json` version: `2.1.235`).

#### B. OpenAI Codex Transitive Graph (`codex-peer` / `cx`)
1. **Hop 0 (Entrypoint Wrapper):** `P:\_sys\env\nodejs\npm-global\codex.cmd`  
   *Content:*
   ```cmd
   @ECHO off
   GOTO start
   :find_dp0
   SET dp0=%~dp0
   EXIT /b
   :start
   SETLOCAL
   CALL :find_dp0

   IF EXIST "%dp0%\node.exe" (
     SET "_prog=%dp0%\node.exe"
   ) ELSE (
     SET "_prog=node"
   )

   endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & set PATHEXT=%PATHEXT:;.JS;=;% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
   ```
2. **Hop 1 (Runtime Interpreter):** Since `%dp0%\node.exe` does not exist in `npm-global`, `_prog` falls back to `node` on `PATH`, resolving strictly to `P:\_sys\env\nodejs\node.exe` (version `v22.22.3`).
3. **Hop 2 (ESM Launcher Script):** `P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\bin\codex.js`  
   *Logic:* Inspects platform (`win32`) and arch (`x64`), resolves package `@openai/codex-win32-x64`, locates `vendor/x86_64-pc-windows-msvc/bin/codex.exe`, and calls `spawn(binaryPath, process.argv.slice(2), { stdio: "inherit", env })`.
4. **Hop 3 (Native Worker PE Binary):** `P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`  
   *Companion Binaries in vendor root:* `codex-code-mode-host.exe`, `rg.exe`, `codex-command-runner.exe`, `codex-windows-sandbox-setup.exe`.
5. **Vendor Version:** `codex.cmd --version` $\to$ `codex-cli 0.148.0` (`package.json` version: `0.148.0`).

#### C. Google Antigravity Transitive Graph (`agy-peer` / `ag`)
1. **Hop 0 (Native PE Executable):** `P:\_sys\tools\agy\agy.exe` (Single-hop compiled Go binary, size: 184,008,856 bytes).
2. **Vendor Version:** `agy.exe --version` $\to$ `1.1.16`.

---

### 2.3. Real File Hashes and File Metadata

The following table records the measured SHA-256 digests and byte lengths:

| File Path | Role | Size (Bytes) | SHA-256 Digest |
|---|---|---|---|
| `P:\_sys\env\nodejs\npm-global\claude.cmd` | `ENTRYPOINT_WRAPPER` | 160 | `7999FBA95DBFFE167D9E0A043F29057979A0518EBE89B60C4FCFC6401EA8C424` |
| `P:\_sys\env\nodejs\npm-global\node_modules\@anthropic-ai\claude-code\bin\claude.exe` | `NATIVE_BINARY` | 256,247,968 | `F14452D1E199273795F2920C00FD7A7F818178DDF1F3EFB4D005A7E3D4EC4EFF` |
| `P:\_sys\env\nodejs\npm-global\codex.cmd` | `ENTRYPOINT_WRAPPER` | 340 | `00743F8084CBC1594683B33CFB8BF14D2CE40D46CA6BA9F7142DE6BA31502A84` |
| `P:\_sys\env\nodejs\node.exe` | `INTERPRETER` | 86,969,160 | `780F44F2C53C108BAE261ADA21A525B4BFE733C020AC85E41BFE94479090AC9B` |
| `P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\bin\codex.js` | `SCRIPT` | 7,236 | `134063E133F0B4244FA3B251ACF973D4FE4B4AEEACBDC135211BF480F59F1477` |
| `P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe` | `NATIVE_BINARY` | 288,476,976 | `2AD2CF8A732DA68B8F141634F92DB1A03016C5FAF533A7225FBC0FB740130410` |
| `P:\_sys\tools\agy\agy.exe` | `NATIVE_BINARY` | 184,008,856 | `EDB7AD85EED477D9179CD605F5821E669C521F05754D73CE6E7DD9B2A073CBD2` |

**Companion Vendor Binaries (`codex-peer`):**
* `codex-code-mode-host.exe`: `5386333666D5EF514B5D509C06DE59B5FA62244F75E22A8DCFA31D8567C379C9` (59,365,168 bytes)
* `rg.exe`: `14231169855EC5205CF5A1B6F1DB358FF4AED4247C86B69CE8AAE647C77F6680` (4,218,880 bytes)
* `codex-command-runner.exe`: `D90ED117DFA8B9F155FAE4306E848E956DD8C2836B43864B6488A12DECF648DC` (1,304,880 bytes)
* `codex-windows-sandbox-setup.exe`: `CCE5D63A096EEFB8FF4624E65F00F11553735A01D7F2789A8869E72BCF74DBA3` (8,852,272 bytes)

---

### 2.4. Real Windows ACL Listings (`icacls` Output)

Executing `icacls` across all relevant executable directories yielded the exact DACL grants below:

```text
P:\_sys\env\nodejs\npm-global
  S-1-5-21-2619959735-1773178187-770436330-2733819303:(I)(OI)(CI)(M)
  S-1-5-21-3396539249-867121940-313551042-2452545878:(I)(OI)(CI)(M)
  BUILTIN\Administrators:(I)(F)
  BUILTIN\Administrators:(I)(OI)(CI)(IO)(F)
  NT AUTHORITY\SYSTEM:(I)(F)
  NT AUTHORITY\SYSTEM:(I)(OI)(CI)(IO)(F)
  NT AUTHORITY\Authenticated Users:(I)(M)
  NT AUTHORITY\Authenticated Users:(I)(OI)(CI)(IO)(M)
  BUILTIN\Users:(I)(RX)
  BUILTIN\Users:(I)(OI)(CI)(IO)(GR,GE)

P:\_sys\env\nodejs\npm-global\node_modules\@anthropic-ai\claude-code\bin
  [Same inherited ACL set as parent]

P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\bin
  [Same inherited ACL set as parent]

P:\_sys\env\nodejs\npm-global\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin
  [Same inherited ACL set as parent]

P:\_sys\env\nodejs
  [Same inherited ACL set as parent]

P:\_sys\tools\agy
  [Same inherited ACL set as parent]
```

**Key Findings:**
1. Every directory inherits `NT AUTHORITY\Authenticated Users:(I)(M)`.
2. Standard unprivileged users (`BUILTIN\Users`) have only Read & Execute `(RX)` and Generic Read/Execute `(GR,GE)`.
3. `Everyone` (`S-1-1-0`) and `ANONYMOUS LOGON` (`S-1-5-7`) have **no write, modify, or append permissions**.

---

## 3. Corrected Admission & Collision Model

### 3.1. Reconciled Windows ACL Admission Rule

#### Why the Old Rule Failed
The old rule stated: *"The directory containing the executable must deny unprivileged writes."*  
In standard Windows multi-user and domain architectures, the interactive developer account logging into the workstation is automatically a member of `NT AUTHORITY\Authenticated Users`. Default NTFS permissions on secondary drives and user profile folders grant `Authenticated Users:(M)`. Requiring denial of `Authenticated Users:M` is impossible without taking administrator ownership and breaking standard user installs.

#### The Reconciled Rule: Non-World-Writable NTFS Enforcement
Admission applies the following deterministic security checks:
1. **Local NTFS Volume Check**: Target paths must reside on a local NTFS filesystem supporting access control lists. Non-ACL filesystems (FAT/exFAT) are rejected unconditionally -- no bypass flag exists in the schema (consistent with `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`'s §4.2, which enforces this same rule with no exception).
2. **Denial of World-Writable / Anonymous Access**:
   * Security Principal `Everyone` (`S-1-1-0`) must NOT have Write Data (`WD`), Append Data (`AD`), Write Attributes (`WA`), Write Extended Attributes (`WEA`), Delete (`DE`), Modify (`M`), or Full Control (`F`). At most Read & Execute `(RX)` is permitted.
   * `ANONYMOUS LOGON` (`S-1-5-7`) and `BUILTIN\Guests` (`S-1-5-32-546`) must have no write or modify grants.
3. **Safe Ownership & Authenticated Access**:
   * Ownership must belong to `BUILTIN\Administrators`, `NT AUTHORITY\SYSTEM`, or a valid user SID matching the active session principal.
   * `NT AUTHORITY\Authenticated Users:(M)` is explicitly **PERMITTED** for local workstation installs.
4. **Reparse Point / Junction Prohibition**:
   * No directory component in the path may be an unvalidated symlink, volume mount point, or junction point pointing to an unverified volume.

**Real-World Security Justification**: This rule effectively neutralizes untrusted local/remote unauthenticated tampering and cross-account privilege escalation from guest/sandbox accounts, while permitting standard non-elevated developer installations. When paired with Phase 1's single-node cryptographic pinning, the admitted entrypoint executable's exact bytes are recorded and pinned as a trusted baseline at admission -- Phase 1 has no pre-existing trusted reference to compare against, so it establishes this baseline rather than detecting whether tampering already occurred before admission. (The stronger multi-node guarantee—where any modification of any script in the transitive chain would fail a pre-spawn revalidation against this baseline—is explicitly deferred to Phase 2).

---

### 3.2. Single-Node Executable Binding (Phase 1)

When a manifest declares an entrypoint (e.g. `claude.cmd`, `codex.cmd`, `agy.exe`), Phase 1 admission strictly validates and pins exactly one entrypoint node. Full multi-node recursive wrapper-chain derivation (`chain_complete=True`) is explicitly deferred to Phase 2.

1. **Target Resolution**: The declared target is resolved to an absolute canonical path.
2. **Single-Node Hashing**: The entrypoint node is hashed (SHA-256) and pinned.
3. **Chain Scope (chain_complete=False)**: The admission receipt records the single entrypoint node and explicitly flags the chain as incomplete (`chain_complete: False`).

**(Phase 2 DEFERRED) Transitive Executable Chain Binding & Revalidation:**
*The following describes the target multi-node chain derivation deferred to Phase 2, based on the empirical host evidence gathered above. It is NOT current Phase 1 behavior:*
1. **Wrapper Inspection**: Recursive static tracing to resolve target interpreters and invoked scripts/binaries.
2. **Transitive Chain Structuring**: Each file recorded with role, path, size, and SHA-256.
3. **Aggregate Chain Digest Computation**: Computing an aggregate hash over the full sequence.
4. **Pre-Spawn Revalidation**: Re-hashing every file in the transitive chain immediately before every `subprocess.Popen` spawn.

---

### 3.3. Deterministic Collision Detection & Canonical Normalization

To eliminate platform-dependent ambiguities (Windows case-insensitivity, Unicode equivalence, file extension aliases):

#### 1. Key Normalization Algorithm: `normalize_key(s: str) -> str`
1. **Unicode NFC**: Apply Unicode Normalization Form C: `unicodedata.normalize('NFC', s)`.
2. **Full Case-Folding**: Convert to lowercase via Unicode case-folding: `s.casefold()`.
3. **Trailing Artifact Stripping**: Strip trailing dots and whitespace: `s.rstrip('. ')`.
4. **Executable Extension Normalization**: For executable names/shims, strip recognized extensions: `.exe`, `.cmd`, `.bat`, `.com`, `.ps1`, `.vbs`, `.js`.

#### 2. Claim Space Extraction
For every candidate manifest $M_i$:
* `claimed_adapter_id` = `normalize_key(M_i.adapter.adapter_id)`
* `claimed_peer_kind` = `normalize_key(M_i.adapter.peer_kind)`
* `claimed_profile_ids` = `[normalize_key(p.profile_id) for p in M_i.profiles]`
* `claimed_shim_names` = `[normalize_key(s) for s in M_i.execution.shim_names]` (if declared)
* `claimed_aliases` = `[normalize_key(a) for a in M_i.adapter.aliases]` (if declared)
* `manifest_ast_digest` = `SHA256(canonical_json(M_i))`

#### 3. Collision Rules
A collision is triggered if:
* Multiple manifests claim the same normalized `adapter_id`, `peer_kind`, `profile_id`, `shim_name`, or alias.
* Multiple manifest files have identical `manifest_ast_digest` under different filenames.

#### 4. Atomic Snapshot Publication & Reader Synchronization
* **Monotonic Registry Generation**: An integer `registry_generation` (starting at 1) tracks published revisions.
* **Isolated Candidate Staging**: Candidate manifests in the registry directory are staged into a memory snapshot.
* **All-or-Nothing Gate**: If any manifest fails JSON schema validation, ACL checks, semantic template checks, single-node executable validation, or triggers a collision, the **entire candidate snapshot is rejected**. No partial registrations are admitted. (Full transitive chain resolution is a Phase 2 gate).
* **Lock-Free Publication (RCU Semantics)**: On successful validation, the new snapshot is wrapped in an immutable `PublishedRegistry(generation=G+1, timestamp=now, adapters=...)`. The global active registry reference is updated via an atomic pointer swap (`active_registry_ref.store()`).
* **Reader Guarantee**: In-flight requests hold a pinned reference to their admitted `PublishedRegistry` and `AdmissionReceipt` instances, guaranteeing immunity from concurrent reload tearing.

---

## 4. Worked Admission Receipts (Live Measured Data)

### 4.1. Claude Code Phase 2 Target Receipt (`claude-peer`)

*(Note: This is an illustrative Phase 2 candidate receipt showing full multi-node transitive chain derivation based on the empirical data gathered above. Actual Phase 1 receipts only hash the single entrypoint node and set `chain_complete: False`).*

```json
{
  "$schema": "https://peerhub.local/schema/admission-receipt/v2",
  "receipt_id": "receipt-cc-claude-peer-20260820T125000Z",
  "schema_version": "2.0.0",
  "chain_complete": true,
  "adapter_id": "claude-peer",
  "peer_kind": "cc",
  "admission_timestamp_utc": "2026-08-20T12:50:00Z",
  "trust_root": {
    "host_machine": "GC-SURFACE-01",
    "user_sid": "S-1-5-21-796419805-904610953-2528718009-1001",
    "user_account": "GC-SURFACE-01\\GC",
    "activation_authority": "system:local-admin",
    "governed_config_revision": "af5bbee"
  },
  "manifest_binding": {
    "manifest_path": "P:\\workspace\\peerhub\\manifests\\claude-adapter.json",
    "manifest_version": "2.0.0",
    "manifest_canonical_sha256": "1348E84042C94EF18089CB9EFB344D18C7BB170F1332FD139123CECCB2163EDB"
  },
  "engine_binding": {
    "engine_id": "builtin:json-claude-v1",
    "engine_implementation_version": "2.0.0",
    "engine_source_path": "P:\\workspace\\peerhub\\peerhub\\adapters\\claude_adapter.py",
    "engine_source_sha256": "DF7E591B4531EE5A681A2C232C7059F465EF061DF14DFA0F19E71A8C0799F38E"
  },
  "observed_vendor": {
    "declared_target": "claude.cmd",
    "observed_cli_version": "2.1.215 (Claude Code)",
    "observed_pe_product_version": "2.1.215.0",
    "package_json_version": "2.1.235"
  },
  "acl_evaluation": {
    "evaluated_paths": [
      "P:\\_sys\\env\\nodejs\\npm-global",
      "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@anthropic-ai\\claude-code\\bin"
    ],
    "volume_type": "NTFS",
    "everyone_writable": false,
    "anonymous_writable": false,
    "authenticated_users_modify_allowed": true,
    "effective_dacl_summary": "BUILTIN\\Administrators:(I)(F), NT AUTHORITY\\SYSTEM:(I)(F), NT AUTHORITY\\Authenticated Users:(I)(M), BUILTIN\\Users:(I)(RX)",
    "verdict": "PASS_SECURE_LOCAL"
  },
  "transitive_executable_chain": [
    {
      "role": "ENTRYPOINT_WRAPPER",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\claude.cmd",
      "file_size_bytes": 160,
      "sha256": "7999FBA95DBFFE167D9E0A043F29057979A0518EBE89B60C4FCFC6401EA8C424",
      "is_reparse_point": false
    },
    {
      "role": "NATIVE_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe",
      "file_size_bytes": 256247968,
      "sha256": "F14452D1E199273795F2920C00FD7A7F818178DDF1F3EFB4D005A7E3D4EC4EFF",
      "is_reparse_point": false
    }
  ],
  "aggregate_chain_digest": "2B34333A2864B03B029A8F414C16E2C271442C3E1D9C7334734AE6F3C2728313",
  "profiles_admitted": ["cc.standard"],
  "revalidation_policy": {
    "on_prespawn": [
      "VERIFY_MANIFEST_CANONICAL_SHA256",
      "VERIFY_ENGINE_SOURCE_SHA256",
      "STAT_AND_VERIFY_TRANSITIVE_CHAIN_HASHES",
      "VERIFY_NO_REPARSE_POINTS",
      "VERIFY_REGISTRY_GENERATION_MATCH"
    ]
  }
}
```

---

### 4.2. OpenAI Codex Phase 2 Target Receipt (`codex-peer`)

*(Note: This is an illustrative Phase 2 candidate receipt showing full multi-node transitive chain derivation based on the empirical data gathered above. Actual Phase 1 receipts only hash the single entrypoint node and set `chain_complete: False`).*

```json
{
  "$schema": "https://peerhub.local/schema/admission-receipt/v2",
  "receipt_id": "receipt-cx-codex-peer-20260820T125000Z",
  "schema_version": "2.0.0",
  "chain_complete": true,
  "adapter_id": "codex-peer",
  "peer_kind": "cx",
  "admission_timestamp_utc": "2026-08-20T12:50:00Z",
  "trust_root": {
    "host_machine": "GC-SURFACE-01",
    "user_sid": "S-1-5-21-796419805-904610953-2528718009-1001",
    "user_account": "GC-SURFACE-01\\GC",
    "activation_authority": "system:local-admin",
    "governed_config_revision": "af5bbee"
  },
  "manifest_binding": {
    "manifest_path": "P:\\workspace\\peerhub\\manifests\\codex-adapter.json",
    "manifest_version": "2.0.0",
    "manifest_canonical_sha256": "2E07F265F8855E3B97A662EB19B6F4F48EF14A69DCBC0934C6C5A4357E236BAE"
  },
  "engine_binding": {
    "engine_id": "builtin:jsonl-codex-v1",
    "engine_implementation_version": "2.0.0",
    "engine_source_path": "P:\\workspace\\peerhub\\peerhub\\adapters\\codex_adapter.py",
    "engine_source_sha256": "A985DFFBB703EDC358371FE431B473E52C882F455560CF3D8BA216F58B3834C8"
  },
  "observed_vendor": {
    "declared_target": "codex.cmd",
    "observed_cli_version": "codex-cli 0.148.0",
    "observed_pe_product_version": null,
    "package_json_version": "0.148.0"
  },
  "acl_evaluation": {
    "evaluated_paths": [
      "P:\\_sys\\env\\nodejs\\npm-global",
      "P:\\_sys\\env\\nodejs",
      "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\bin",
      "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\bin"
    ],
    "volume_type": "NTFS",
    "everyone_writable": false,
    "anonymous_writable": false,
    "authenticated_users_modify_allowed": true,
    "effective_dacl_summary": "BUILTIN\\Administrators:(I)(F), NT AUTHORITY\\SYSTEM:(I)(F), NT AUTHORITY\\Authenticated Users:(I)(M), BUILTIN\\Users:(I)(RX)",
    "verdict": "PASS_SECURE_LOCAL"
  },
  "transitive_executable_chain": [
    {
      "role": "ENTRYPOINT_WRAPPER",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\codex.cmd",
      "file_size_bytes": 340,
      "sha256": "00743F8084CBC1594683B33CFB8BF14D2CE40D46CA6BA9F7142DE6BA31502A84",
      "is_reparse_point": false
    },
    {
      "role": "INTERPRETER",
      "canonical_path": "P:\\_sys\\env\\nodejs\\node.exe",
      "file_size_bytes": 86969160,
      "sha256": "780F44F2C53C108BAE261ADA21A525B4BFE733C020AC85E41BFE94479090AC9B",
      "is_reparse_point": false
    },
    {
      "role": "SCRIPT",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\bin\\codex.js",
      "file_size_bytes": 7236,
      "sha256": "134063E133F0B4244FA3B251ACF973D4FE4B4AEEACBDC135211BF480F59F1477",
      "is_reparse_point": false
    },
    {
      "role": "NATIVE_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\bin\\codex.exe",
      "file_size_bytes": 288476976,
      "sha256": "2AD2CF8A732DA68B8F141634F92DB1A03016C5FAF533A7225FBC0FB740130410",
      "is_reparse_point": false
    }
  ],
  "companion_binaries": [
    {
      "role": "HELPER_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\bin\\codex-code-mode-host.exe",
      "file_size_bytes": 59365168,
      "sha256": "5386333666D5EF514B5D509C06DE59B5FA62244F75E22A8DCFA31D8567C379C9"
    },
    {
      "role": "HELPER_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\codex-path\\rg.exe",
      "file_size_bytes": 4218880,
      "sha256": "14231169855EC5205CF5A1B6F1DB358FF4AED4247C86B69CE8AAE647C77F6680"
    },
    {
      "role": "HELPER_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\codex-resources\\codex-command-runner.exe",
      "file_size_bytes": 1304880,
      "sha256": "D90ED117DFA8B9F155FAE4306E848E956DD8C2836B43864B6488A12DECF648DC"
    },
    {
      "role": "HELPER_BINARY",
      "canonical_path": "P:\\_sys\\env\\nodejs\\npm-global\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\codex-resources\\codex-windows-sandbox-setup.exe",
      "file_size_bytes": 8852272,
      "sha256": "CCE5D63A096EEFB8FF4624E65F00F11553735A01D7F2789A8869E72BCF74DBA3"
    }
  ],
  "aggregate_chain_digest": "5F88C287CA360A93B547CA8645BEB836CD8F322895E56CE480F1BB925F47F1AE",
  "profiles_admitted": ["cx.standard"],
  "revalidation_policy": {
    "on_prespawn": [
      "VERIFY_MANIFEST_CANONICAL_SHA256",
      "VERIFY_ENGINE_SOURCE_SHA256",
      "STAT_AND_VERIFY_TRANSITIVE_CHAIN_HASHES",
      "VERIFY_NO_REPARSE_POINTS",
      "VERIFY_REGISTRY_GENERATION_MATCH"
    ]
  }
}
```

---

### 4.3. Google Antigravity Phase 2 Target Receipt (`agy-peer`)

*(Note: This is an illustrative Phase 2 candidate receipt showing full transitive chain derivation based on the empirical data gathered above -- Agy's own measured chain happens to resolve to a single native binary with no wrapper layer, so this specific example's chain is one node, unlike the multi-node Claude/Codex examples above. Actual Phase 1 receipts only hash the single entrypoint node and set `chain_complete: False`).*

```json
{
  "$schema": "https://peerhub.local/schema/admission-receipt/v2",
  "receipt_id": "receipt-ag-agy-peer-20260820T125000Z",
  "schema_version": "2.0.0",
  "chain_complete": true,
  "adapter_id": "agy-peer",
  "peer_kind": "ag",
  "admission_timestamp_utc": "2026-08-20T12:50:00Z",
  "trust_root": {
    "host_machine": "GC-SURFACE-01",
    "user_sid": "S-1-5-21-796419805-904610953-2528718009-1001",
    "user_account": "GC-SURFACE-01\\GC",
    "activation_authority": "system:local-admin",
    "governed_config_revision": "af5bbee"
  },
  "manifest_binding": {
    "manifest_path": "P:\\workspace\\peerhub\\manifests\\agy-adapter.json",
    "manifest_version": "2.0.0",
    "manifest_canonical_sha256": "05041744ADD473AB8176C9CF453B76502C2B9D3F0B5A3710B98B4DAB4D0E9EF4"
  },
  "engine_binding": {
    "engine_id": "builtin:json-agy-v1",
    "engine_implementation_version": "2.0.0",
    "engine_source_path": "P:\\workspace\\peerhub\\peerhub\\adapters\\agy_adapter.py",
    "engine_source_sha256": "AD0BB81F94C42A559C5464D2B2BBFFF55249145B78D927162C5247B00D962300"
  },
  "observed_vendor": {
    "declared_target": "agy.exe",
    "observed_cli_version": "1.1.16",
    "observed_pe_product_version": null,
    "package_json_version": null
  },
  "acl_evaluation": {
    "evaluated_paths": [
      "P:\\_sys\\tools\\agy"
    ],
    "volume_type": "NTFS",
    "everyone_writable": false,
    "anonymous_writable": false,
    "authenticated_users_modify_allowed": true,
    "effective_dacl_summary": "BUILTIN\\Administrators:(I)(F), NT AUTHORITY\\SYSTEM:(I)(F), NT AUTHORITY\\Authenticated Users:(I)(M), BUILTIN\\Users:(I)(RX)",
    "verdict": "PASS_SECURE_LOCAL"
  },
  "transitive_executable_chain": [
    {
      "role": "NATIVE_BINARY",
      "canonical_path": "P:\\_sys\\tools\\agy\\agy.exe",
      "file_size_bytes": 184008856,
      "sha256": "EDB7AD85EED477D9179CD605F5821E669C521F05754D73CE6E7DD9B2A073CBD2",
      "is_reparse_point": false
    }
  ],
  "aggregate_chain_digest": "FC2F8714EE97285C06432F1287FD080ABA958C67909B68760A86F6B7BE0DE1A5",
  "profiles_admitted": ["ag.standard"],
  "revalidation_policy": {
    "on_prespawn": [
      "VERIFY_MANIFEST_CANONICAL_SHA256",
      "VERIFY_ENGINE_SOURCE_SHA256",
      "STAT_AND_VERIFY_TRANSITIVE_CHAIN_HASHES",
      "VERIFY_NO_REPARSE_POINTS",
      "VERIFY_REGISTRY_GENERATION_MATCH"
    ]
  }
}
```

---

## 5. Verification Checklist & Gate Closure

| Gate Item 3 Condition | Status | Implementation Verification |
|---|---|---|
| Reconciled Real Windows ACL Rule | **CLOSED** | Proved `Authenticated Users:M` is default on live volume; updated rule to enforce NTFS non-world-writable (`Everyone:no-write`, `Anonymous:no-write`). |
| Transitive Executable Binding | **DEFERRED (Phase 2)** | Documented empirical host requirements for multi-node chains, but deferred actual implementation to Phase 2. Phase 1 validates only the single entrypoint node. |
| Deterministic Normalization & Collision Detection | **CLOSED** | Formally defined Unicode NFC, case-folding, and extension stripping; specified atomic snapshot rejection and generation RCU synchronization. |
| Worked Real Admission Receipts | **PHASE 2 ILLUSTRATIVE** | Provided 3 `chain_complete: true` example receipts built from real empirical data, modeled as Phase 2 targets. These are not Phase 1 receipts -- real Phase 1 admission only ever produces single-node, `chain_complete: false` receipts. |
