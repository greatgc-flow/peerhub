# TDD Readiness Inventory R1

## Captured legacy evidence

35 contract IDs have digest-bound legacy records. These are regression inputs,
not automatic proof of V1 conformance.

## Required V1 captures before source TDD

`DP-06`, `DT-01..06`, `HR-04..06`, `RT-04..06`, `GB-01`, `GB-03..05`,
`CJ-02`, and `CJ-05` are `V1_SPEC_ONLY`. Each requires a deterministic fake
runner result, canonical transcript, record digest, and status `V1_CAPTURE`.

## Authority proof bindings

Filesystem identity, fencing, JSON crash recovery, external-effect receipts,
recovery probing, drain classification, and quota admission remain
`V1_SPEC_ONLY` until bound to the relevant controlled-fake fixtures and the
authority proof matrix. Therefore authority cutover is intentionally
non-executable before TDD.

## Source start order

The first source deliverable is the provider-free controlled-fake runner; it
unlocks the 19 V1 captures and no production routing, health recovery, or
authority mutation is enabled by that runner.
