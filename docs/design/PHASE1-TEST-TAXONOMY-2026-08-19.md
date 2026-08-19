# Phase 1 Proposal: MECE 5-Tier Test-Suite Taxonomy

> **STATUS: DRAFT, PENDING CX DIALECTICAL REVIEW**
> Expected 2026-08-20 13:29 KST or later

Here is the proposed MECE test-suite taxonomy and execution architecture for establishing complete functional parity between `peerhub` and `hub.py` before retiring Engram's legacy wrapper code.

---

# MECE Test-Suite Taxonomy for Peerhub Independence

To ensure the test suite is **MECE (Mutually Exclusive, Collectively Exhaustive)**, each layer of behavior is assigned exactly one deterministic boundary. No two tiers test the same failure mode, and no gap exists between pure in-memory parsing and live process execution on the host machine.

```
1. STATIC (tests/static): Schema validation, type safety, manifest syntax, command-flag integrity
2. UNIT (tests/unit): Pure in-memory: adapter translation, manifest parser, registry resolution
3. CONTRACT (tests/contract): Boundary contracts, golden stream fixtures, mock PTY/subprocess emulation
4. INTEGRATION (tests/integration): Dispatch engine, multi-peer orchestration, arbitration, lease management
5. LIVE END-TO-END (tests/e2e) [NEW TOP-LEVEL CATEGORY]: Real OS subprocesses, live AI CLI binaries, status-line telemetry parsing
```

## 1. Directory Structure: Existing Directory Allocation vs. Warranted New Top-Level Directory

The existing directory tree at `P:\workspace\peerhub\tests` (`static`, `unit`, `contract`, `integration`) covers hermetic, deterministic testing well. However, **a new top-level category (`tests/e2e/`) is genuinely warranted** rather than overloading `tests/integration/`.

### Existing Directory Role Allocation
*   **`tests/static/` (Code & Configuration Correctness)**: Retains pure linting, type checking (`mypy`/`pyright`), and newly absorbs **declarative manifest schema validation**. It verifies that adapter manifests (JSON/YAML) conform to the generic adapter JSONSchema before any Python code runs.
*   **`tests/unit/` (Isolated In-Memory Logic)**: Absorbs unit-level manifest parsing, profile-to-command resolution, environment variable isolation, and argument escaping without spawning any OS processes.
*   **`tests/contract/` (Wire & Interface Parity)**: Absorbs interface parity validation between `hub.py` public APIs (enforcing **DIR-003**) and peerhub equivalents. It tests adapter output stream parsers against frozen "golden recording" fixtures (raw PTY streams, ANSI-colored output, NDJSON lines).
*   **`tests/integration/` (Hermetic Multi-Component Orchestration)**: Absorbs routing, load-balancing, consensus gathering, arbitration hooks (DIR-005), and state file leases using mock subprocess runners. Every test here must execute fast, offline, and deterministically.

### Why `tests/e2e/` Warranted a Distinct Top-Level Directory
Spawning real CLI binaries on the host machine (`codex.cmd`, `agy`, `claude`) introduces external non-determinism: network latency, API rate limits, provider outages, and live quota depletion. If live tests are mixed into `tests/integration/`, the fast test loop used during daily development is compromised. Isolating live execution in `tests/e2e/` allows:
1. Independent test runner configuration (longer timeouts, retry/probe mechanics, explicit flag activation).
2. Dedicated quota-aware lifecycle hooks that run pre-flight sanity checks before incurring token costs.
3. Hermetic CI/pre-commit runs to run `static + unit + contract + integration` in seconds, while `e2e` runs in scheduled canary or explicit verification mode.

## 2. Layer-by-Layer MECE Coverage Breakdown

### Tier 1: Static Layer (`tests/static/`)
*   **Manifest Validation (`test_manifest_schemas.py`)**: Validates every peer auto-detection manifest against the canonical generic adapter schema. Ensures mandatory fields (discovery commands, default profile flags, permission flags per DIR-002, status-line regexes) are present and structurally valid.
*   **CLI Argument Syntax Integrity (`test_flag_syntax.py`)**: Statically verifies that CLI flag templates do not contain invalid placeholder tokens or contradictory flags (e.g. non-interactive print mode combined with interactive confirmation prompts).

### Tier 2: Unit Layer (`tests/unit/`)
*   **Generic Adapter Engine (`test_adapter_engine.py`)**: Tests the serialization of command-line arrays from profiles and parameters. Verifies platform-specific shell escaping on Windows (handling `cmd.exe` vs PowerShell quoting rules, caret escaping, and whitespace).
*   **Manifest & Capability Registry (`test_registry.py`)**: Verifies manifest discovery precedence, model capability mapping, profile merging (e.g., standard vs effort vs deepthink), and graceful fallback when a declared capability is absent.
*   **Environment & Directive Injector (`test_directive_injection.py`)**: Tests the formatting and appending of standing user directives (DIR-001 through DIR-006) and IPC metadata envelopes without side effects.

### Tier 3: Contract Layer (`tests/contract/`)
*   **Public API & Hub Parity (`test_hub_api_contracts.py`)**: In accordance with **DIR-003**, verifies signature and behavior parity for all public actions (`action_ask`, `_lease_cfg`, `_build_session_cmd`) so consumer scripts cannot distinguish peerhub from legacy `hub.py`.
*   **Golden Stream Normalization (`test_stream_contracts.py`)**: Feeds recorded raw byte streams (containing ANSI escape codes, progress spinners, carriage returns, and PTY chunks) into adapter output parsers to verify that cleaned text and structured event streams match expected golden snapshots.
*   **Mock Subprocess Interception (`test_subprocess_contract.py`)**: Simulates synthetic CLI execution using fake process runners that mimic standard output, error codes, and unexpected process crashes.

### Tier 4: Integration Layer (`tests/integration/`)
*   **Multi-Peer Dispatch & Coordination (`test_dispatch_routing.py`)**: Verifies multi-peer ask fan-out, profile-based routing, timeout handling, and response aggregation across multiple simulated peers.
*   **Arbitration & Consensus Engine (`test_consensus_arbiter.py`)**: Verifies unanimous consensus validation (DIR-006) and the conditional invocation of the smartest-model final arbiter (DIR-005) when dissent or high-risk actions are detected.
*   **Concurrency & Lease Management (`test_session_leases.py`)**: Validates lock contention, session leasing, and atomic state updates under concurrent queries.

### Tier 5: Live End-to-End Layer (`tests/e2e/`)
*   **Live CLI Discovery (`test_live_discovery.py`)**: Uses the manifest discovery engine to probe real binaries currently present on the system (`agy`, `codex.cmd`, `claude`), verifying that detected paths and versions reflect physical reality per **DIR-004**.
*   **Live Execution with Standing Permissions (`test_live_execution.py`)**: Executes a minimal live query through peerhub to each available CLI using non-interactive permission flags (e.g., `cc` with `--dangerously-skip-permissions`, `cx` with `-s workspace-write`, `ag` via PTY mode).
*   **End-to-End Parity Verification (`test_live_parity_comparison.py`)**: Runs parallel minimal prompts through legacy `hub.py` and peerhub's new generic adapter pipeline, verifying identical execution semantics, exit codes, and envelope structure.

## 3. Concrete Plan for Status-Line Output & Telemetry Assertion

Status lines differ significantly across CLIs: some emit ANSI-styled terminal status bars (`agy`), some write human-readable metrics on `stderr` (`codex`), and others format token counters into stream envelopes (`claude`).

```
Raw CLI Process Stream (ANSI escapes, \r carriage returns, stderr tokens)
  -> Stream Normalizer / PTY Pipe (Strips terminal cursor codes, captures status frame)
  -> Per-CLI Status-Line Regex (Extracts Model, Tokens, Cost, Latency)
  -> Telemetry Frame Assertion (Asserts non-empty fields & expected schema per profile)
```

### 1. Declarative Status-Line Definition in Manifest
Each CLI manifest defines a `status_line` parser specification containing:
*   `stream`: Target stream (`stdout`, `stderr`, or `pty_combined`).
*   `pattern`: Regular expression capturing named groups: `(?P<model>[\w\.\-]+)`, `(?P<input_tokens>\d+)`, `(?P<output_tokens>\d+)`, and `(?P<latency_ms>\d+)`.
*   `fallback_strategy`: How to extract status if the CLI runs in non-interactive print mode (e.g. trailing JSON block vs header line).

### 2. Stream Normalization & ANSI Scrubbing
For PTY-dependent CLIs (such as `ag` on Windows):
*   A dedicated normalizer captures the raw byte stream, separates in-place cursor updates (`\r`) from completed lines (`\n`), and applies an ANSI-escape stripper.
*   The raw status-line buffer is preserved in a structured telemetry object attached to the query result.

### 3. Assertion Protocol in E2E Tests
The test suite asserts the following invariants for every live CLI invocation:
1.  **Status Line Extraction**: `assert result.telemetry.status_line is not None`.
2.  **Model Confirmation**: The extracted model matches or resolves to the requested profile model.
3.  **Monotonic Metrics**: Token counts and latency fields are positive integers.
4.  **No Relay Frame Pollution**: Terminal formatting codes do not leak into the user-facing response payload.

## 4. Quota Exhaustion vs. Silent-Skipping Strategy (Anti-Flake / Anti-Rot)

A major risk in live E2E testing is either **flakiness** (failing the test suite when a live provider returns a 429 quota exhaustion, as cx recently experienced) or **test rot** (silently skipping failing tests, creating a false sense of security).

To resolve this, `peerhub` will implement an explicit **Tri-State Lifecycle with Pre-Flight Probes and Anti-Rot Accounting**:

```
[E2E Test Triggered] -> Pre-Flight Quota Probe -> either [Probe: Quota Active] -> Execute Real E2E -> [PASS] or [FAIL, Real Defect]
                                                or [Probe: 429 Exhausted] -> Mark QUOTA_QUARANTINE -> Logged in Drift Report with Resume Timestamp (Does NOT pass silently)
```

### 1. Pre-Flight Health Probes
Before executing heavy E2E prompts, the test runner executes a low-cost pre-flight probe (e.g., a 1-token query or CLI status check).
*   If the CLI returns a valid response, the test proceeds to full validation.
*   If the probe returns an authentication failure, network error, or standard crash, the test **FAILS immediately**.
*   If the probe returns a verified quota exhaustion error, the test transitions to **Quarantine State**.

### 2. Explicit Quota Quarantine State (Not a Generic Skip)
Instead of invoking `pytest.skip()` without context, the test runner records an explicit status:
`STATUS = QUOTA_QUARANTINED`
*   The test must extract and attach the provider's reported reset timestamp (e.g. `2026-08-20 13:29` for cx) and the raw error output.
*   The result is written to a machine-readable `test_execution_manifest.json`.

### 3. Anti-Silent-Skip Gating & Accounting
To prevent real regressions from hiding behind skips:
*   **Default E2E Invariant**: A suite-level assertion verifies that at least one primary peer CLI executed successfully. If 100% of live peer tests are quarantined/skipped, the E2E suite exits with code `2` (Inconclusive / No Live Coverage), blocking any promotion or deletion of Engram wrappers.
*   **Explicit Flag Gating**: Live E2E tests only run when `--live-cli` or `--live-cli-all` is passed. In normal CI, hermetic Contract and Integration tests (using mock recordings) guarantee functional regression coverage, while live E2E tests run on a scheduled canary matrix.

## 5. Uncertainties & Items Flagged for cx Counter-Critique

When cx resets its quota on 2026-08-20 13:29, the following three architectural questions should be submitted for counter-critique:

1.  **Windows PTY vs Pipes in Live E2E**: `ag` requires PTY mode (`requires_pty=true`) on Windows, whereas `cx` runs with `-s workspace-write` via standard pipe redirection. Is a unified pseudo-terminal wrapper in `tests/e2e` sufficient for both, or should non-PTY peers strictly use direct `asyncio.subprocess` pipes to avoid terminal buffering quirks?
2.  **Streaming Telemetry vs Buffered Parsing**: Should status-line verification in E2E tests occur strictly after process exit (buffered), or must the test suite assert incremental line delivery during real-time streaming to catch mid-flight hanging?
3.  **Legacy `hub.py` Side-by-Side Dual Run Cost**: During the parity verification phase, running identical queries through both legacy `hub.py` and new `peerhub` doubles token consumption. Should side-by-side dual runs be restricted to cheap models (`cc.standard`, `cx` base), while expensive profiles rely strictly on Tier 3 contract golden recordings?
