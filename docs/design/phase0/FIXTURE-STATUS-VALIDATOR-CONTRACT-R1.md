# Fixture Status Validator Contract R1

Status: proposed executable acceptance contract; it authorizes no live
operation and no Phase 0 exit by itself.

## Inputs

The validator consumes `fixtures/CONTRACT.md`, `fixtures/fixture-status-v1.json`,
the canonical fixture-record directory, `hub-actions-v1.csv`, and
`action-fixture-policy-v1.csv`. It treats the 54 IDs in the behavioral
contract as the only Phase 0 behavioral exit set. AC companion IDs are checked
only by the separately named cutover gate and are never silently included in
the 54-ID result.

## Required decisions

1. A behavioral exit check fails unless every one of the 54 IDs has status
   `V1_CAPTURE`, a record whose ID matches, and digests that verify against
   the referenced raw artifacts. `LEGACY_CAPTURE`, `V1_SPEC_ONLY`, and
   `BLOCKED_LIVE_CAPTURE` fail that check.
2. An action-linkage check fails unless each legacy action resolves through
   exactly one policy domain and every referenced positive, invalid, and
   authorization fixture is known. When invoked in exit mode, each referenced
   fixture must also satisfy decision 1.
3. A cutover check fails unless AC-01..AC-09 are all `V1_CAPTURE` with
   verified records and the separately required cutover ratification is
   present. It must not infer success from an identifier row or specification.

## Measured red/green proof

The first implementation of this validator must preserve two checked test
artifacts: (a) the current overlay, which fails behavioral exit and action
linkage exit mode because statuses are not `V1_CAPTURE`; and (b) an isolated
synthetic all-captured fixture set with matching test records, which passes.
The synthetic green proves validator semantics only; it cannot change the
real overlay, Phase 0 state, or cutover state. The test suite must assert both
outcomes in one run.
