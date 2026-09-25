# PEERHUB-SIMPLIFICATION-R5-ag-deepthink-2026-09-25

**Status**: **PROPOSED** (Round 5)
**Guiding Principle**: "매우 단순함이 최선" (very simple is the best). Default to cutting functionality/ceremony unless load-bearing.

This document proposes a comprehensive architecture and CLI simplification plan for `peerhub`. It evaluates findings from prior reviews against the current codebase and provides concrete KEEP/SIMPLIFY/DELETE verdicts, incorporating corrections and protecting actively ratified consensus/quarantine work.

---

## 1. CLI Surface Simplification

The `peerhub` CLI currently exposes exactly 30 root commands via `peerhub/cli/__init__.py` (4,974 lines / 236,007 bytes). The current `argparse` structure contains significant boilerplate but successfully powers all current workflows.

### 1.1 Findings & Verdicts

| Area / Command | Finding | Verdict | Reason |
|---|---|---|---|
| `cli/__init__.py` | Large manual `argparse` file. | **SIMPLIFY (Optional/Deferred)** | Continue extracting commands to distinct files (like `daily.py`) using `argparse`. A mandatory migration to `typer` is deferred until a concrete type-safety/reduction case justifies the framework swap. |
| `duty` / `session` | Related to room operations. | **SIMPLIFY** | Nest under `room`: `peerhub room duty` and `peerhub room session`. **Must preserve** `duty sweep`'s cross-room semantics without imposing a mandatory `--room-id` filter. |
| `gate` | Checks open/closed peer status. | **SIMPLIFY** | Consolidate under `health precheck` or explicitly retire the shell contract after checking callers. It is a predicate (exit 0/1), not just a status display. |
| `adapter discover` | Grouped under root. | **SIMPLIFY** | Move to `peer adapter-discover` or `peer discover`. Ensure `cli/commands/setup.py`, README, and parser tests are updated. |
| `broker` | Exposes unfinished-effect data. | **SIMPLIFY** | Move to `diag broker`. This is real effect data, not an obsolete class leak, so it should be visible under diagnostics. |
| `alert` | Inherently room-scoped. | **SIMPLIFY** | Move to `room alert` to enforce context, preserving existing dispatch/audit behavior. |
| `room thread-new` | Compatibility alias for `create-thread` with legacy semantics (slug/default-creator). | **DELETE** | Retire compatibility adapter entirely. It drops distinct legacy semantics rather than a literal alias. |
| `room unreact` | Redundant alias. | **DELETE** | `react --remove` already exists; only the `unreact` alias is removed. |
| `statusline` | PeerHub command formatting. | **DELETE** | No current hook consumer of this PeerHub command was found. Remove formatter, registration, and docs together. **Note:** Must NOT delete the separate `_sys/ai/common/statusline/statusline-unified.sh` telemetry hook. |
| `diag --live` | Replace `cls` with ANSI codes. | **DONE** | Item already implemented in `cli/commands/daily.py`. Removed from backlog. |
| `workspace`, `config`, `backup`, `lease`, `routing` | Root commands out of scope for this simplification round. | **KEEP** | Genuinely out of scope for this round; retain as-is to ensure table is MECE for all 30 root commands. |

---

## 2. Architectural Ceremony Reduction

The original architecture introduced distributed-systems patterns (CQRS, Event Sourcing, Capability Leasing). We must simplify these while retaining the concrete security and consistency boundaries relied upon by the active consensus and quarantine workflows.

### 2.1 Findings & Verdicts

| Component | Finding | Verdict | Reason |
|---|---|---|---|
| `ApplicationAPI` | Gateway routing mapping CLI args to `CommandEnvelope`. | **SIMPLIFY** | Target direct service calls, but **only after** extracting the explicit minimal authorization checks (ingress validation, credential mapping) required by the active consensus contracts. |
| `CapabilityLease` | Validation for attempt, target, tier, policy, and enforcement. | **SIMPLIFY** | Preserve the required validations. Direct OS permissions do not replace credential verification or policy checks. Collapse the proof representations, but retain the guard. |
| `ArtifactMaterializer` | Manages file staging, digest verification, path containment, and recovery. | **SIMPLIFY** | Preserve required content-digest verification, resolved source/staging path containment, recovery, and verified-artifact reservation, atomically with dispatch intent (see `peerhub/dispatch/artifacts.py` ~line 181 and `peerhub/application/workflows.py` ~line 864). It cannot be blindly replaced with `tempfile` without explicitly cutting the dependent artifact contract (which is load-bearing). |
| `GovernanceBroker` & Outbox | Synchronous `BEGIN IMMEDIATE` SQLite transaction handling CAS, idempotency, and receipts. | **REJECT AS SPECIFIED** | The broker is already synchronous. It cannot be summarily removed because current consensus and proposal effects rely on atomic intent and recovery. Must inventory real vs no-op effects, then design a smaller equivalent before removal. |
| `CompletionContract` | Manages delivery contracts (ask/broadcast). | **SIMPLIFY** | Preserve response-present/truncation/error distinctions. Audit and cut unsupported or unused non-delivery ceremony (e.g., generic wire decoder accepting unsupported kinds). |

---

## 3. Domain-Specific Over-Engineering

### 3.1 Findings & Verdicts

| Domain | Finding | Verdict | Reason |
|---|---|---|---|
| **Feedback Journal** | Dedicated subsystem for recording unstructured feedback. | **DELETE** | Completely remove `governance/feedback.py` and its entire vertical: runtime construction, API registration, CLI, and docs. Export existing user records if needed. |
| **Arbiter-Review** | Automated model arbiter dispatch with verified evidence. | **KEEP** | This is a current dependency for active consensus (B1/B8) and standing arbiter directives. It does not use human prompts. Revisit later if the consensus design changes. |
| **Operational-Error Tracking** | Producer of `REQUESTED` quarantine reviews via CAS-protected counters. | **SIMPLIFY** | Must retain the counter and `REQUESTED`-review producer workflow. Do not replace with plain logging, as that breaks the consumer (`quarantine_review.py`) fixed tonight. Simplify the underlying storage logic only. |
| **Quarantine Review** | Consumes operational errors; requires synchronous MANUAL/SECURITY dominance. | **SIMPLIFY** | The health call is already synchronous. Any simplification must preserve MANUAL/SECURITY dominance, pending recovery, and receipt identity. It must explicitly share a transaction with the health storage. |

---

## 4. Conflict Avoidance & Coordination

To ensure this simplification does not undo real work committed during recent sessions, the following guardrails apply:

1. **Consensus Replacement**: The implementation of `CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md` (B1, B5, B6, B8 specs) is active. The consensus contracts require atomic approval/receipt/outbox commits, authority-version checks in the broker, public command-to-receipt binding, and arbiter evidence attachment. **Simplifications touching gateway authorization (Items 15/16) and Broker (Item 17) are sequenced strictly AFTER consensus completion.** *Acceptance Criterion:* Any gateway-bypass or broker-replacement work must explicitly demonstrate it preserves ALL B1-B8 contract semantics (commit/claim authority and freshness checks, pre-invocation revalidation, atomic approval/receipt/effect intent, command replay binding, exclusive effect recovery, cutover fencing). Waiting for a timing gate alone does not authorize removing these semantics; they must be verified as preserved.
2. **Quarantine Escalate Pipeline**: `OperationalErrorService` produces `REQUESTED` reviews that `QuarantineReviewCoordinator` consumes. This exact pipeline was just fixed. Simplifications to error tracking must preserve the producer/consumer relationship and the creation of review identities.

---

## 5. MECE Actionable Items Table

The work breakdown separates technical dependencies (must happen before) from scheduling preferences (this session's priority).

### 5.1 CLI Surface & Organization
*Scheduling Preference: Early (Low Risk)*

| ID | Action Item | Target Vertical (Full Cleanup Ownership) | Technical Dependencies |
|---|---|---|---|
| 01 | Delete `statusline` PeerHub command, formatter module, and registration. | `cli/__init__.py`, formatter module, docs | None |
| 02 | Delete `gate` command group (consolidate to health precheck). | `cli/__init__.py`, `api.py` | None |
| 03 | Delete `room thread-new` and legacy adapter export. | `cli/__init__.py`, `application/thread_new.py` | None |
| 04 | Move `duty` and `session` under `room`. Keep `duty sweep` cross-room. | `cli/__init__.py`, `api.py` | None |
| 05 | Move `alert` under `room` group. | `cli/__init__.py`, `api.py` | None |
| 06 | Move `adapter discover` to `peer discover`. | `cli/__init__.py`, `cli/commands/setup.py`, docs | None |
| 07 | Move `broker` to `diag broker`. | `cli/__init__.py` | None |
| 08 | Remove `room unreact` redundant alias. | `cli/__init__.py` | None |
| 09 | Extract modules using `argparse`. *Typer migration is deferred.* | `cli/__init__.py`, `cli/*.py` | None |
| 09a | Explicitly retain `workspace`, `config`, `backup`, `lease`, `routing`. | CLI root | None |

### 5.2 Domain & Architecture Simplification
*Scheduling Preference: Staged (Medium/High Risk)*

| ID | Action Item | Target Vertical (Full Cleanup Ownership) | Technical Dependencies |
|---|---|---|---|
| 10 | Delete Feedback Journal subsystem entirely. | `governance/feedback.py`, API, CLI, docs, runtime | None |
| 11 | Audit/cut unsupported `CompletionContract` non-delivery ceremony. | `application/direct_ask.py`, `broadcast.py`, core, completion assessor, wire decoder, persisted-contract compatibility surface | None |
| 12 | Simplify Operational-Error storage, **preserving `REQUESTED` review producer**. | `governance/operational_errors.py`, DB | None |
| 13 | Simplify Quarantine Review, ensuring shared atomic tx with health. | `application/quarantine_review.py`, DB | Needs PRESERVED PRODUCER CONTRACT and shared health/gov transaction support from item 12 |
| 14 | Simplify ArtifactMaterializer file logic, **preserving content-digest verification, resolved source/staging path containment, recovery, and verified-artifact reservation, atomically with dispatch intent** (`artifacts.py` ~L181, `workflows.py` ~L864). | `dispatch/materializer.py`, `artifacts.py` | None |
| 15 | Inventory/design minimal common gateway ingress authorization checks. | `application/governance_authorizer.py` | None (Read-only design sub-item) |
| 15a| Inventory/design dispatch CapabilityLease validation requirements. | `dispatch/capability.py` | None (Read-only design sub-item) |
| 16 | Extract gateway ingress authorization code directly to services. | `cli/*.py`, `application/api.py` | Consensus Completion |
| 16a| Extract CapabilityLease validation code. | `cli/*.py`, `dispatch/capability.py` | Consensus Completion |
| 17 | Inventory real vs no-op effects; design minimal atomic recovery store. | `governance/broker.py`, `persistence/*` | Consensus Completion |

*(Arbiter removal and blind Broker deletion have been explicitly rejected and removed from the actionable backlog.)*
