# HR-04..06 V1 Fixture Spec R1

AG/CX cross-reviewed deterministic fake-clock/host-probe cases.

- HR-04: auto health circuit may be cleared only by a matching current receipt;
  manual/security/policy quarantine remains closed until its required authority clears it.
- HR-05: an administrative action authorizes one recovery probe only; it never
  writes HEALTHY or opens a gate directly.
- HR-06: failed current-fingerprint probe keeps `CIRCUIT_OPEN` and increments
  backoff; success opens only when incident, gate generation, timestamp, and
  fingerprint match under CAS. Stale or changed-fingerprint receipts are no-ops.

All cases use an isolated journal, fake clock, exact-production fake adapter,
and no provider call. They become source TDD tests only after Phase 0 ratification.
