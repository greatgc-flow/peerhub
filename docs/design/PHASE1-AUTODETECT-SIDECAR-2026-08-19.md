# Phase 1 Proposal: Auto-detecting AI CLIs via Sidecar Manifests

> **STATUS: DRAFT, PENDING CX DIALECTICAL REVIEW**
> Expected 2026-08-20 13:29 KST or later

Here is my phase 1 design proposal for the `peerhub` AI-CLI auto-detection and Engram wrapper inventory. I look forward to your counter-position, `cx`.

### 1. Auto-Detection Mechanism: "Sidecar Manifests + Generic Subprocess Adapter"

**The Core Problem:** We cannot securely or efficiently run `--peerhub-probe` on every arbitrary executable found in `PATH` to check if it's an AI CLI. An unknown binary might ignore the flag and execute destructive default behavior or hang indefinitely.

**The Proposal:**
I propose a **Declarative Manifest + Generic Adapter** model. Discovery must be passive (reading files) rather than active (executing binaries).

1. **The Contract:** For a CLI to be recognized by `peerhub`, it must provide a `.peerhub-adapter.json` manifest. This manifest defines its `peer_kind`, `cli_name`, supported `profiles` (e.g., `standard`, `deepthink`), and the argv/env translation rules required to invoke it.
2. **Discovery Layer 1 (Push/AppData):** Installers can drop this manifest into a centralized, standard OS location (e.g., `%APPDATA%\peerhub\adapters.d\` or `~/.config/peerhub/adapters.d/`).
3. **Discovery Layer 2 (Pull/PATH):** For portable environments (like Engram), when `peerhub` resolves `PATH`, it doesn't just look for executables. If it sees `C:\tools\gemini.exe`, it looks for a sibling file named `C:\tools\gemini.peerhub-adapter.json`.
4. **Generic Subprocess Adapter:** Instead of writing a new Python class (like the hardcoded `RealAgyAdapter`) for every new tool, `peerhub.adapters.registry` uses a `GenericManifestAdapter`. This adapter reads the JSON manifest to know how to translate an `AdapterRequest` into the `argv` array for the target CLI. *If* complex custom logic is needed, the manifest can point to a Python entry point, but the default should be declarative configuration.

### 2. Full MECE Inventory: Engram to Peerhub Transition

To make Engram purely a portable dev environment with ZERO AI-CLI wrapper logic, every single file in `_sys/cli` and `_sys/core` (69 files total) has been exhaustively classified.

### Bucket 1: AI-CLI-wrapper logic moving to peerhub (55 files)
These wrappers and coordinators must transition before Engram can drop them.

| Engram Component | Peerhub Coverage | Proposed Peerhub Owner / Action |
| :--- | :--- | :--- |
| `_sys/cli/_bat-shim` | **GAP** | **`peerhub.application.shims`**. Needs equivalent generation logic. |
| `_sys/cli/ag_statusline.py` | **GAP** | **`peerhub.application.console`**. Console display logic. |
| `_sys/cli/agy` | **GAP** | **`peerhub.application.shims`**. Bash shim. |
| `_sys/cli/agy.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/agy_entry.py` | **GAP** | **`peerhub.application.shims`**. Python entry shim. |
| `_sys/cli/batch-review` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/batch-review.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/batch_review.py` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/claude` | **GAP** | **`peerhub.application.shims`**. Bash shim. |
| `_sys/cli/claude.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/claude_entry.py` | **GAP** | **`peerhub.application.shims`**. Python entry shim. |
| `_sys/cli/codex` | **GAP** | **`peerhub.application.shims`**. Bash shim. |
| `_sys/cli/codex.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/codex_entry.py` | **GAP** | **`peerhub.application.shims`**. Python entry shim. |
| `_sys/cli/collab-rate-gate` | **GAP** | **`peerhub.governance.quota`**. Governance logic. |
| `_sys/cli/collab-rate-gate.bat` | **GAP** | **`peerhub.governance.quota`**. Governance logic. |
| `_sys/cli/console_runner.py` | **Partial** (`peerhub.dispatch`) | **`peerhub.application.runtime`**. Core CLI event loop. |
| `_sys/cli/diag` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/diag.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/diag.py` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/git-draft` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/git-draft.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/git_draft.py` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/hub` | **GAP** | **`peerhub.application.shims`**. Bash shim. |
| `_sys/cli/hub.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/launch` | **GAP** | **`peerhub.application.shims`**. Bash shim. |
| `_sys/cli/launch.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/launcher.py` | **Partial** (`peerhub.dispatch`) | **`peerhub.application.runtime`**. Core CLI event loop. |
| `_sys/cli/manage` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/manage.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/manage.py` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/msg` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/msg.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/peer_console.py` | **GAP** | **`peerhub.application.console`**. Interface implementation. |
| `_sys/cli/peer_mgr.py` | **GAP** | **`peerhub.application.console`**. Interface implementation. |
| `_sys/cli/peerhub.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/cli/set-collab-rate` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/cli/set-collab-rate.bat` | **GAP** | **`peerhub.application.cli`**. Make peerhub subcommand. |
| `_sys/core/dispatch.bat` | **GAP** | **`peerhub.application.shims`**. Windows shim. |
| `_sys/core/dispatcher.py` | **Partial** (`peerhub.routing`) | **`peerhub.routing`**. Name resolution and routing. |
| `_sys/core/hub.py` | **Partial** (`peerhub.dispatch`) | **`peerhub.application.runtime`**. Core loop. |
| `_sys/core/hub_config.json` | **Partial** | **`peerhub.application.runtime`**. Core config. |
| `_sys/core/hub_context.py` | **Partial** | **`peerhub.core.execution`**. Execution context setup. |
| `_sys/core/hub_error.py` | **GAP** | **`peerhub.core.execution`**. Error handling. |
| `_sys/core/hub_health.py` | **GAP** | **`peerhub.application.cli`**. Diagnostics implementation. |
| `_sys/core/hub_interceptor.py` | **GAP** | **`peerhub.core.execution`**. Extensibility layer. |
| `_sys/core/hub_logging.py` | **Partial** | **`peerhub.application.runtime`**. Logging logic. |
| `_sys/core/hub_peer.py` | **Partial** (`peerhub.routing`) | **`peerhub.routing`**. Peer definition. |
| `_sys/core/hub_profile_router.py` | **Partial** (`peerhub.routing`) | **`peerhub.routing`**. Route parsing. |
| `_sys/core/launcher.py` | **Partial** (`peerhub.dispatch`) | **`peerhub.application.runtime`**. App orchestration. |
| `_sys/core/operational_guard_matrix.py` | **GAP** | **`peerhub.governance`**. Policy rules. |
| `_sys/core/provisioner.py` | **GAP** | **`peerhub.governance`** & **`peerhub.core.execution`**. Environment staging. |
| `_sys/core/quota.py` | **GAP** | **`peerhub.governance.quota`**. Quota enforcement. |
| `_sys/core/quota_capabilities.py` | **GAP** | **`peerhub.governance.quota`**. Quota models. |
| `_sys/core/registrar.py` | **Covered** (`registry.py`) | **Delete**. Replaced by dynamic registry. |

### Bucket 2: Portable-dev-environment-bootstrap logic (9 files)
These correctly stay in Engram forever with no AI-CLI awareness.

| Engram Component | Classification / Rationale |
| :--- | :--- |
| `_sys/core/doctor.py` | Portable dev environment health checks and diagnostics. |
| `_sys/core/env_loader.py` | Environment variable setup for the portable OS space. |
| `_sys/core/pathlayout.py` | Defines expected layout of the portable environment. |
| `_sys/core/relocator.py` | Handles moving/copying the portable directory structure. |
| `_sys/core/setup.py` | General dev environment bootstrap. |
| `_sys/core/tidy_temp.py` | Cleanup of generic temporary files in Engram. |
| `_sys/core/updater.py` | Updating the Engram skeleton itself. |
| `_sys/core/version_resolver.py` | Resolving Node/Python toolchains. |
| `_sys/core/virtualizer.py` | Managing isolated virtual environments. |

### Bucket 3: Unsure / Flag for Dialectical Round (5 files)
These files are ambiguous and require dialectical review to determine ownership.

| Engram Component | Reason for Flagging |
| :--- | :--- |
| `_sys/cli/cleanup.py` | Is this cleaning AI session data (Bucket 1) or general dev temps (Bucket 2)? |
| `_sys/core/config.py` | Is this purely Engram configuration (Bucket 2) or AI-centric variables (Bucket 1)? |
| `_sys/core/scrubber.py` | Does this scrub AI logs/PII (Bucket 1) or general toolchain output (Bucket 2)? |
| `_sys/core/snapshot.py` | Snapshotting workspace state for AI (Bucket 1) or dev backups (Bucket 2)? |
| `_sys/core/timestamps.py` | Generic utility, likely Bucket 2, but needs review on its exact coupling. |

### 3. Assumptions and Ambiguities (For Dialectical Focus)

`cx`, I want to focus our debate on these specific vulnerabilities in my proposal:

1. **Assumption (Shim Generation):** I am assuming we want `peerhub` to physically write `.bat`/`.sh` shims into a directory on the `PATH` upon discovery so the user can still just type `claude`. The alternative is forcing the user to type `peerhub run cc`. *Do we generate shims, or do we enforce a strict `peerhub <command>` interface?* Shim generation can be messy regarding lifecycle management (updates/deletions).
2. **Assumption (Generic Adapter Viability):** I am assuming a JSON manifest is expressive enough to translate a complex `AdapterRequest` (with workspaces, profiles, and routing constraints) into an `argv` array for arbitrary future CLIs like Gemini. *Will a purely declarative JSON manifest hit a ceiling where we actually NEED a Python plugin system to handle CLI-specific quirks?*
3. **Ambiguity (Engram Coupling):** `peerhub` is supposed to be independent, but it needs to know about Engram's `_sys/ai/user-directives.md` and `.ai/state.json`. If we move `provisioner.py` to `peerhub.core.execution`, how do we prevent `peerhub` from just becoming tightly coupled to Engram's specific directory layout?

### 4. 5-Whys on the Proposal

*   **Why use a Sidecar Manifest?**
    *   To allow `peerhub` to map abstract CLI binaries to its internal `PeerAdapter` API.
*   **Why not just execute the binary to ask for its mapping (`--probe`)?**
    *   Because executing untrusted/unknown binaries found on `PATH` is a severe security flaw and unacceptably slow.
*   **Why not use an existing Python plugin framework like `pluggy` or `importlib.metadata`?**
    *   Because those are designed for discovering *Python packages* in a virtual environment. We are discovering system binaries (`.exe`, `.cmd`, Go/Rust binaries) that have no concept of Python entry points. The lowest common denominator for arbitrary executable discovery is the file system (sidecars/AppData).
*   **Why require `peerhub` to take over `provisioner.py`?**
    *   Because the wrapper logic requires preparing a sandbox before executing the peer. If Engram is strictly a "portable dev environment" and `peerhub` handles all AI coordination, the act of "providing the environment to the AI" crosses the boundary.
*   **Why might this fail?**
    *   It fails if the declarative manifest isn't flexible enough. If every AI CLI has completely different concepts of "profiles" and "system prompts", a JSON mapping might become too complex, forcing us back to writing custom Python adapters for each new tool.
