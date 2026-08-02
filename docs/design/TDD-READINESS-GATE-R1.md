# TDD Readiness Gate R1

PeerHub source TDD may start only when every condition is true:

1. The fixture inventory contains an explicit status for all 54 contract IDs.
   `V1_SPEC_ONLY` and `LEGACY_CAPTURE` are valid design evidence but fail this
   gate; every required behavioral conformance case must become `V1_CAPTURE`.
2. The controlled-fake runner contract is frozen: isolated root, append-before-
   reduce journal, canonical transcript, deterministic clock/IDs, no provider.
3. Health implementation tests adopt R3: host-only receipt minting, incident /
   gate-generation CAS, authority-scoped quarantine clearing, and separate
   health from quota/pacing admission.
4. Protocol tests use the R1 crosswalk and canonical error taxonomy.
5. Authority-cutover proof-matrix rows bind explicit fixture IDs/statuses; no
   cutover is executable while a required proof remains spec-only.
6. A new ratification round binds protocol, authority, fixture, and dependency
   hashes without rewriting historical frozen documents.

## First red tests after this gate

1. controlled fake event journal and DP-06 / DT-01..06;
2. R3 health transition suite HR-04..06;
3. routing RT-04..06;
4. broker GB-01/03/04/05;
5. CLI envelopes CJ-02/05;
6. fixture inventory/authority proof acceptance gate.

No source file or package scaffold is authorized by this document alone.
