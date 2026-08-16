# Windows-Native Brokered Read-Only Reducers Design

**Date:** 2026-08-16
**Target Subsystem:** `peerhub/adapters/`
**Status:** RATIFIED WITH IMPLEMENTATION GATES (round 1 ag.deepthink
draft; round 2 cc.effort independent critique found the design NOT
READY -- an asserted-not-demonstrated Prerequisite-4 timing claim, an
undisclosed `pywin32` dependency, an undisclosed process-spawn privilege
requirement, and a worktree-vs-system-wide Low-IL scoping mismatch, all
independently re-verified against real source by the terminal; round 3
ag.deepthink revision closed items 1-3 concretely and, per DIR-004
discipline, correctly left item 4 -- `agy`'s actual write footprint --
as an honest open empirical question rather than a guess). Unlike the
other two designs ratified today, **implementation may not proceed
until 3 empirical preconditions are resolved**, all named in Section 3
and Section 8: (a) the process-spawn privilege set
(`SeIncreaseQuotaPrivilege`/`SeAssignPrimaryTokenPrivilege` or
`SeImpersonatePrivilege`) is actually available in the target
environment; (b) `agy.exe`'s real write footprint is determined and
either shown to stay inside the worktree or given an explicit relabeled
allow-list; (c) the network-egress question (does `READ_ONLY` require
blocking outbound calls) is answered. This is a design ratification
only -- treat it the same as the health/quota tracking design's canary
gate, not a green light to start coding.

## 1. Concrete OS-level Mechanism

**Decision:** We will use **Windows Mandatory Integrity Control (MIC)** by launching the adapter process with a **Low Integrity Level (Low IL)** token, combined with a **Restricted Token** (via `CreateRestrictedToken`) that drops specific write privileges.

**Dependencies:** This mechanism requires adding `pywin32` as a new project dependency. `subprocess.Popen()` lacks support for these primitives; manipulating integrity levels and calling `CreateRestrictedToken` requires `pywin32` bindings to `advapi32.dll` and `kernel32.dll`.

**Justification:** 
The Accord explicitly mandates "no heavy containers". Windows does not have Linux-style namespaces, but the Integrity mechanism is a native, instantaneous, and highly robust security primitive. By default, the worktree and standard user files are labeled at Medium Integrity Level. A Low IL process is natively blocked by the Windows kernel from modifying Medium IL files or directories. This enforces a strict Read-Only boundary at the OS kernel level without the overhead of Windows Sandbox, AppContainers, or complex cross-account ACL management.

## 2. Definition of "Reducer"

In this architecture, a "reducer" is an OS-level enforcement boundary (the machine-owned launcher) that *reduces* the ambient authority of the parent `peerhub` process. While `peerhub` runs with full Medium/High IL access and can mutate the workspace, the reducer strips these capabilities before handing execution to the adapter. 

For the `READ_ONLY` tier, this means the operation set is restricted strictly to:
* **Allowed:** Reading files, traversing directories, and executing non-mutating shell commands.
* **Blocked:** Creating, modifying, appending, deleting, or renaming any files or directories within the worktree.

## 3. The Empirical Negative Probe

To satisfy Errata Prerequisite 3, we must prove the boundary actively blocks writes rather than passively trusting a CLI flag.

**Concrete Test:**
1. **TEST NEEDED - Privilege Availability:** Before testing enforcement, the runner must verify that the environment possesses the required privileges to spawn a new process under a modified token. This requires either `CreateProcessAsUser` (needs `SeIncreaseQuotaPrivilege` and `SeAssignPrimaryTokenPrivilege`) or `CreateProcessWithTokenW` (needs `SeImpersonatePrivilege`). We cannot silently assume these are available to a non-elevated dev/CI account.
2. The test runner creates a temporary Medium IL worktree directory.
3. The runner invokes the target adapter via the new Brokered Low IL Launcher, passing an explicit instruction/script to create and write to `proof_of_write.txt` in that directory.
4. The probe **passes** if and only if the OS returns an `ERROR_ACCESS_DENIED` to the child process AND the test runner verifies `proof_of_write.txt` does not exist on disk.

This proves that even if an adapter attempts a malicious or accidental mutation, the OS boundary guarantees it fails.

## 4. SEC-01 Git-Diff Guard Timing & Applicability

**Clarification:** The `SEC-01` Git-diff guard is strictly a **post-condition** relative to OS process execution. It executes after the adapter process has run to verify no unexpected changes occurred. 

Because execution occurs *after* the `dispatch intent commits` (the database transaction in PeerHub), a Git-diff guard **cannot** serve as a pre-commit gate. If the OS boundary failed, the damage would already be written to disk before `SEC-01` could run. 

Therefore, **this Brokered Read-Only Reducer IS the mechanism that satisfies Prerequisites 1-3 for the `READ_ONLY` tier.** The `SEC-01` guard does *not* satisfy the prerequisite for a pre-execution/pre-commit gate; only the OS-level launcher and its pre-spawn enforcement receipt can do that.

## 5. First-Target Adapter

**Target:** `RealAgyAdapter` (`agy`)

**Rationale:** As documented in Errata Section 8, `agy` currently has no enforceable filesystem confinement and violates workspace bounds. Applying the Read-Only Reducer to `agy` provides immediate, measurable security value and serves as the perfect empirical proof of the new boundary.

## 6. Scope Boundary (What is Excluded)

The following are strictly excluded from this increment:
* **Higher Tiers:** `WORKTREE_WRITE`, `GIT_MUTATE`, and `REMOTE_MUTATE` capabilities are not addressed.
* **Other Adapters:** `claude` and `codex` adapters remain unconfined in this iteration.
* **Network Reducers:** Strict egress firewalling or network isolation is deferred.
* **Cross-Platform:** macOS and Linux (namespaces/cgroups) implementations are deferred.

## 7. Honest Accounting against Errata Section 8 Prerequisites

| Prerequisite | Status in this Increment | Justification |
|--------------|--------------------------|---------------|
| **1. Machine-owned launcher prepares & attests control before execution** | **CLOSED** | The new `LowIntegrityLauncher` actively drops the token IL before spawning the process and yields an `ENFORCED` (or `CONFINED`) receipt. |
| **2. Observation bound to canonical digest** | **CLOSED** | The launcher will hash the exact `agy.exe` path and arguments, binding the enforcement receipt to this `plan_digest`. |
| **3. Empirical negative probe** | **CLOSED** | The Low IL negative write test guarantees the OS enforces the block. |
| **4. Post-plan gate compares receipt before dispatch intent commits** | **CLOSED** | We will create the restricted/Low-IL token as an independent pre-flight step BEFORE receipt construction (~line 668), verify its properties via `GetTokenInformation`, bind the receipt to that verified token, and then thread that EXACT token handle ~240 lines down to a modified spawn call that uses it. This requires modifying `peerhub/dispatch/pipe.py`'s launcher and `dispatch_and_execute()`'s control flow to carry the token handle through `create_attempt`/materialization/`record_dispatch_intent`. |

## 8. Unresolved Questions

1. **`agy.exe` Write Footprint (System-Wide Low IL Constraints):** Windows Mandatory Integrity Control has no concept of a "worktree". A Low-IL token blocks writes to EVERY Medium-IL-or-higher object system-wide. If `agy.exe` needs to write to Medium IL paths outside the worktree (e.g., session/cache state, logs, temp files) for normal operation, a blanket Low-IL token will break `agy` outright, not just confine it. Because `agy`'s write footprint cannot currently be determined from available information, this is an open empirical question that MUST be answered before implementation. We must either investigate and design an explicit allow-list of paths to relabel for the process's own operation, or prove it does not need to write outside the worktree.
2. **Network Egress at Low IL:** While Low IL blocks Medium IL file writes, its effect on network sockets is less strict. If `READ_ONLY` must also mean "no network egress", Low IL alone may not suffice without Windows Firewall rules or dropping network SIDs. We need to decide if `READ_ONLY` strictly prohibits network calls for `agy`.
