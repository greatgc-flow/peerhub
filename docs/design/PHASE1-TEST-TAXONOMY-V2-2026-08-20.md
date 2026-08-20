# Phase 1: Test Taxonomy and Promotion Matrix (v2)

> **STATUS: DRAFT v2 (Post-Round 1 Debate)**

This document establishes the test taxonomy and promotion criteria for Phase 1, prioritizing empirical transport boundaries and the five-state promotion matrix.

## 1. Execution Boundary and Transport

Based on the empirical probe (`PTY-BUFFERING-PROBE-2026-08-03.md`), the premise that `ag` requires Windows ConPTY is explicitly **abandoned**. 

All live E2E testing and production transports will utilize **plain pipes (`subprocess.Popen`)**.
- **Mitigation Requirement**: To prevent stdin hangs, `pipe.py` must explicitly handle stdin EOF (e.g., `stdin=subprocess.DEVNULL` or immediate `stdin.close()`) for `ag` and `cx`.
- **Consistency**: The live test suite must use the exact same transport model as the production runner, without forcing PTY wrappers that mask underlying pipe regressions.

## 2. API Parity and Observable Behavior

"Public API parity" must not target private legacy helpers (such as `_lease_cfg`). Parity is defined exclusively by the **observable execution boundary** recorded in the action inventory:
- Argv structure
- Exit code
- Stdout/Stderr envelope
- Persisted filesystem effects
- Recovery semantics

## 3. Invariants and Liveness Telemetry

Streaming status lines and token metrics are **not** universal cross-provider invariants.
- **Buffering**: Non-interactive profiles often block-buffer output until exit. Tests must not universally mandate streaming extraction.
- **Telemetry**: If a profile does not expose runtime model selection or token metrics, the valid assertion state is `ABSENT` (with evidence), not test failure. Streaming assertions are reserved for profiles empirically shown to emit incremental frames.

## 4. The Promotion Matrix

The previous tri-state quota model is replaced with a strict five-state promotion matrix for each capability in the declared required capability matrix:

1. `EXECUTED_PASS`: Deterministic and live OS-boundary proofs succeeded.
2. `PRODUCT_FAILURE`: The adapter, transport, or core logic failed an observable parity check.
3. `QUOTA_BLOCKED`: External provider rejected the request due to quota. Externally inconclusive, blocks capability promotion but does not fail generic CI.
4. `ENVIRONMENT_UNAVAILABLE`: Preflight failures (authentication, provider outage, missing toolchain). Externally inconclusive.
5. `NOT_REQUESTED`: The capability was not targeted in this test slice.

### 4.1 Required Evidence
Every live result must be accompanied by raw evidence receipts:
- Observed CLI version
- Exact argv profile
- Transport used
- Timestamps
- Parser version

"At least one peer executed" is no longer sufficient. Every declared capability must achieve `EXECUTED_PASS` before full release.
