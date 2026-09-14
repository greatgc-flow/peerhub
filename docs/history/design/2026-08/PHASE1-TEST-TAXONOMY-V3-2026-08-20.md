# Phase 1 Test Taxonomy V3

## 1. Test Tier Structure

This version restores the foundational five-tier structure, which defines primary execution boundaries, nondeterminism sources, permitted overlaps, file/marker placement conventions, CI default behaviors, and release-gate ownership.

### Static
* **Primary Execution Boundary:** No execution of system code. Analyzes source text, AST, or configuration files natively.
* **Primary Nondeterminism Source:** None (purely deterministic based on rulesets).
* **Permitted Defense-in-Depth Overlap:** May overlap with unit tests for syntax/type assertions, but strictly avoids execution.
* **File & Marker Placement Convention:** `tests/static/` or inline lint configurations.
* **Default CI Inclusion/Exclusion:** Included by default (fast feedback loop).
* **Release-Gate Ownership:** Build/Static Analysis toolchain.

### Unit
* **Primary Execution Boundary:** Process-bound execution of isolated functions, classes, or modules. External IO (network, disk) is fully mocked or stubbed.
* **Primary Nondeterminism Source:** Hash seed randomization, OS thread scheduling (if multithreaded unit), time (if not mocked).
* **Permitted Defense-in-Depth Overlap:** May overlap with contract tests for internal interface adherence.
* **File & Marker Placement Convention:** `tests/unit/` with `test_*.py` pattern.
* **Default CI Inclusion/Exclusion:** Included by default.
* **Release-Gate Ownership:** Component Developer.

### Contract
* **Primary Execution Boundary:** Boundary between two isolated services or adapters, using localized consumer-driven contracts or synthesized provider endpoints.
* **Primary Nondeterminism Source:** Message serialization timing, mock provider response latency.
* **Permitted Defense-in-Depth Overlap:** Overlaps with integration tests for basic wire-protocol validation and unit tests for data marshaling.
* **File & Marker Placement Convention:** `tests/contract/` using provider/consumer subdirectories.
* **Default CI Inclusion/Exclusion:** Included by default (runs hermetically without real external dependencies).
* **Release-Gate Ownership:** API/Adapter Owner.

### Integration
* **Primary Execution Boundary:** Interaction between our system and a real (but controlled/local) dependency, such as a local database, controlled real-OS executable, or containerized provider.
* **Primary Nondeterminism Source:** Local network latency, filesystem IO, subprocess startup timing.
* **Permitted Defense-in-Depth Overlap:** Overlaps with contract tests on edge cases, and end-to-end tests for subsystem readiness.
* **File & Marker Placement Convention:** `tests/integration/` (requires `@pytest.mark.integration`).
* **Default CI Inclusion/Exclusion:** Included by default in expanded CI pipelines, but may require specific runner capabilities (e.g., Docker).
* **Release-Gate Ownership:** Subsystem/Platform Owner.

### End-to-End (E2E)
* **Primary Execution Boundary:** Full system execution spanning live environments, connecting to live remote providers.
* **Primary Nondeterminism Source:** Remote provider outages, real-world network partitions, remote rate-limiting (quota blocked).
* **Permitted Defense-in-Depth Overlap:** Very narrow overlap; should only test critical user journeys that cannot be reliably simulated in integration.
* **File & Marker Placement Convention:** `tests/e2e/` (requires `@pytest.mark.e2e`).
* **Default CI Inclusion/Exclusion:** Excluded from default/PR CI to prevent quota exhaustion and flakiness; runs on scheduled or release pipelines.
* **Release-Gate Ownership:** Product/QA Owner.

## 2. Multidimensional Promotion Ledger

To address the limitations of a single-dimensional state enum, the test promotion tracking relies on a multidimensional ledger.

**Ledger Key:**
`coverage_case_id` × `peer_instance (or profile binding)` × `platform (or architecture)` × `transport` × `proof_kind`

**Proof Kinds (at least):**
* `deterministic contract or integration`
* `controlled real-OS executable`
* `live provider exact-profile`
* `legacy-parity evidence`

**Cell Data Structure:**
Every cell in the ledger carries the following:
* **Applicability:** `REQUIRED`, `OPTIONAL`, or `NOT_APPLICABLE`
* **Attempt Outcome:** The latest outcome of the test attempt (derived from the five-state classifier).
* **Evidence Reference:** Link/URI to the recorded execution receipt or log.
* **Freshness:** Timestamp of the execution.
* **Relevant Digests:** Manifest, adapter, or config digests associated with the run.
* **Invalidation Conditions:** Triggers that mark the cell data as stale (e.g., manifest changes, adapter drift).

**Full Promotion Success:**
Full promotion succeeds **only when every current `REQUIRED` cell in the multidimensional ledger registers a passing attempt outcome**.

## 3. Five-State Classifier & Precedence

The outcome of an attempt is classified using the following five states, with explicit reason codes to differentiate failures and missing preconditions.

**States:**
* `EXECUTED_PASS`
* `PRODUCT_FAILURE`
* `QUOTA_BLOCKED`
* `ENVIRONMENT_UNAVAILABLE`
* `NOT_REQUESTED`

**Reason Codes:**
* `MISSING_EXECUTABLE`: Required CLI binary is not found on the host.
* `AUTHENTICATION_FAILURE`: Invalid or missing credentials for a live provider.
* `NETWORK_FAILURE`: Unable to reach remote endpoints.
* `PROVIDER_OUTAGE`: Remote provider returns 5xx or known outage response.
* `HARNESS_FAILURE`: Test framework or infrastructure crashed independently of the product code.

**Precedence & Resolution of Contradictory Evidence:**
If contradictory evidence exists between two `proof_kind` values for the same cell (e.g., `controlled real-OS executable` passes but `live provider exact-profile` fails), the system respects the dimensionality of the ledger rather than attempting a simplistic merge. However, because promotion requires every `REQUIRED` cell to pass, any failure in a `REQUIRED` proof kind blocks promotion regardless of other successes. The precedence respects the distinct environments: a pass in a local deterministic test does not override a failure in a live exact-profile test, and both exist distinctly within the ledger.

## 4. Transport Binding & Invalidation

The previous transport conclusion overcorrected based on narrow evidence. The transport behavior is corrected as follows:

* **Narrow Claim:** The tested `cc`, `ag`, and `cx` invocations worked via pipes with the specific observed EOF behavior. This does not guarantee all future adapters, profiles, versions, or production transports will permanently use pipes.
* **Binding:** Keep current bindings pinned to `PIPE` on a per exact adapter and profile basis.
* **Invalidation Trigger:** The transport probe is explicitly invalidated and a re-probe is triggered upon any `executable`, `version`, or `argv` drift.
* **PTY Availability:** Controlled PTY test cases remain available. The frozen `InvocationPlan` transport and process-boundary contracts still support future PTY cases.
* **EOF Handling:** Stdin EOF handling is a **generic runner invariant**. The runner will close stdin whenever no further input is expected, rather than relying on provider-name branching (i.e., it applies generically, not just to `ag` and `cx` specifically).
