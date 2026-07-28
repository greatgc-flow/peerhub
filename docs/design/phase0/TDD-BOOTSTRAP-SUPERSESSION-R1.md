# Controlled-Fake TDD Bootstrap Supersession R1

Status: proposed narrow supersession. This document changes no historical
file and takes effect only when its raw-byte hash is bound by a unanimous
bootstrap ratification.

`TDD-READINESS-GATE-R1.md` condition 1 is impossible if read as a prerequisite
for the provider-free runner: the runner is the first authorized mechanism
that can create the missing V1 captures. For that runner alone, this document
supersedes the pre-TDD portion of condition 1 as follows:

1. Red tests and source for the provider-free controlled-fake runner may begin
   while behavioral IDs are `V1_SPEC_ONLY` or `LEGACY_CAPTURE`.
2. The runner must remain within `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`: fresh
   root, injected clock/IDs, deterministic event scripts, append-before-reduce
   journal, and no provider, live Hub, authority cutover, or host mutation
   broker.
3. This exception does not satisfy, relax, or defer the Phase 0 behavioral
   exit: all original 54 IDs still require verified `V1_CAPTURE` records.
4. AC-01..AC-09 are not runner-exit IDs. They remain `V1_SPEC_ONLY` until
   separately captured and ratified for authority cutover.
5. No production feature, authority cutover, or host mutation broker becomes
   authorized by this supersession.
