# Phase 1: Auto-Detection and Manifest Security Model (v2)

> **STATUS: DRAFT v2 (Post-Round 1 Debate)**

This document revises the auto-detection and sidecar design, incorporating the strict capability-based security model and bridge architecture established in the Phase 1 dialectical debate.

## 1. Capability & Consumer Crosswalk (Replacing File Buckets)

While the initial inventory correctly identified the 69 files across `_sys/cli` and `_sys/core` (as verified by the terminal), migrating files directly carries over host assumptions. Instead, migration is driven by a capability crosswalk.

### 1.1 Core Capabilities
- **Adapter Resolution**: Discovering and binding a peer capability (e.g., `codex`, `claude`) to an executable and manifest.
- **Transport & Execution**: Invoking the executable via plain pipes with stdin mitigation (see Test Taxonomy v2).
- **Session Management**: Owning `.peerhub` state without coupling to legacy `.ai` paths.

### 1.2 The `provisioner.py` Split
The legacy `provisioner.py` currently mixes concerns and must be split:
- **Generic Toolchain**: Installation of Node, Python, and jq remains owned by Engram.
- **Adapter Setup**: Resolving and validating peer adapters becomes PeerHub's responsibility.

## 2. Manifest Security Model

Passive discovery is powerful but must not become an execution vector for arbitrary commands.

### 2.1 Explicit Scanning
PeerHub will **not** scan arbitrary executables on the PATH. It will only scan for `*.peerhub-adapter.json` files within explicitly configured, trusted adapter directories.

### 2.2 Typed Schema & Fail-Closed Precedence
Manifests must adhere to a strict, typed schema:
- **No Shell Interpolation**: Invocation parameters must be an array of strings (argv). Templating is limited to explicitly enumerated placeholders (e.g., `${PEERHUB_PROMPT}`, `${PEERHUB_WORKSPACE}`).
- **No Python Entry Points**: The escape hatch allowing manifests to point to arbitrary Python code is removed. All behavior must be declarable in the strict JSON schema.
- **Fail-Closed Collisions**: If multiple manifests claim the same peer alias (e.g., two manifests for `codex`), PeerHub fails closed rather than resolving via directory order.

## 3. The Engram Bridge

To prevent PeerHub core from coupling to Engram-specific layouts (like `_sys/ai/user-directives.md` or `.ai/state.json`), we introduce an optional `peerhub-engram` bridge.

### 3.1 Bridge Interfaces
Core PeerHub will consume typed data through interfaces. The bridge implements these for the Engram host:
- `DirectiveSource`: Provides standing rules and user directives.
- `LegacyStateReader`: Translates legacy `.ai/state.json` into typed session objects.
- `HostProvisioningPort`: Coordinates adapter requirements with the host's generic toolchain.

A PeerHub installation without the Engram bridge remains a supported, first-class configuration.

## 4. Compatibility Shims

Discovery must never silently write PATH shims or shadow existing executables. Optional compatibility shims (e.g., generating `cc.bat` to point to PeerHub) are explicitly provisioned, versioned, and removed by an owned lifecycle command, distinct from discovery.
