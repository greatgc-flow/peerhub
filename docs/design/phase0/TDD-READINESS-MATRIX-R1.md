# TDD Readiness Matrix R1

**Status:** Proposed planning artifact. It is an inventory of present evidence, not an implementation, capture-promotion, Phase 0 exit, or cutover authorization.

## Invariants

- The behavioral set is exactly 54 IDs in `fixtures/fixture-status-v1.json`.
- 35 are `LEGACY_CAPTURE`; 19 are `V1_SPEC_ONLY`.
- Every behavioral entry has `phase0_exit_eligible: false`; none is currently a V1 capture.
- `AC-01` through `AC-09` are nine distinct authority-proof fixtures in `authority-proof-status-v1.json`. They are not part of the 54 behavioral set and cannot change its Phase 0 count.

## Behavioral inventory

| Family | `LEGACY_CAPTURE` | `V1_SPEC_ONLY` | Current evidence caveat |
|---|---|---|---|
| DP | DP-01, DP-02, DP-03, DP-04, DP-05 | DP-06 | DP-02/DP-03 share `DP-02-03.transcript.json`; its named cases are legacy coverage only. |
| DT | — | DT-01, DT-02, DT-03, DT-04, DT-05, DT-06 | No legacy capture status in this family. |
| SL | SL-01, SL-02, SL-03, SL-04, SL-05, SL-06 | — | SL-05 has a payload record but no transcript artifact. |
| CR | CR-01, CR-02, CR-03, CR-04, CR-05, CR-06 | — | All six have legacy payload records. |
| CS | CS-01, CS-02, CS-03, CS-04, CS-05, CS-06 | — | CS-02 and CS-05 have payload records but no transcript artifact. |
| HR | HR-01, HR-02, HR-03 | HR-04, HR-05, HR-06 | Three legacy captures and three specifications only. |
| RT | RT-01, RT-02, RT-03 | RT-04, RT-05, RT-06 | Three legacy captures and three specifications only. |
| GB | GB-02, GB-06 | GB-01, GB-03, GB-04, GB-05 | Two legacy captures and four specifications only. |
| CJ | CJ-01, CJ-03, CJ-04, CJ-06 | CJ-02, CJ-05 | Four legacy captures and two specifications only. |

Totals: 35 `LEGACY_CAPTURE`, 19 `V1_SPEC_ONLY`, 0 exit-eligible.

## What is safe to prepare before TDD

The following preparation is provider-free and does not convert evidence state:

1. Validate the overlay's fixed 54-ID membership, unique IDs, allowed status vocabulary, and all-false exit flags.
2. Build an explicit evidence index by fixture ID, rather than by file glob. Its raw artifact check must precede parsing or normalization.
3. Preserve missing and shared transcript facts exactly: no transcript for CS-02, CS-05, or SL-05; one shared legacy transcript for DP-02/DP-03.
4. Bind any future test to the applicable fixture specification and frozen error/certainty vocabulary.

These checks may report legacy completeness or an integrity failure. They MUST NOT report `V1_CAPTURE`, write the status overlay, or declare Phase 0 exit.

## Future controlled-fake runner prerequisites

These are gate conditions for a later, separately ratified runner scope.

| Prerequisite | Required evidence |
|---|---|
| Single-fixture receipt | One fixture ID, execution receipt, raw artifacts, and a verified raw-byte SHA-256 digest. |
| Fresh execution provenance | The exact controlled-fake runner and baseline revision used; a legacy file cannot be relabeled as fresh execution. |
| Error semantics | Result aligned with the frozen error, certainty, and retry semantics, or a separately ratified divergence record. |
| Namespace isolation | Behavioral evidence remains in the 54-ID set; AC evidence remains in the 9-ID cutover set. |
| Additive custody | Legacy captures remain unchanged; any future record is additive and governed before status promotion. |

Canonicalization beyond raw-byte verification, the final index schema, baseline invalidation, and divergence-register format remain open decisions. They are deliberately not selected by this matrix.
