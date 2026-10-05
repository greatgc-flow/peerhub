# M2 Pre-TDD Contracts (M2.0 Minimal Extension Host, M2.1 Artifact)

Status: **RATIFIED FROZEN.** Drafted 2026-10-05 from the R9 roadmap package (09_ROADMAP/M2_*), mechanically validated
(18 requirements / 60 tests, every requirement linked by >= 2 tests, required dimensions covered, no duplicate ids,
state-machine and exception-catalog test references all resolve). Decisions 1–8 ratified and codified into the contracts.

Files: `M2_0_EXTENSION_HOST_CONTRACT.md`, `M2_1_ARTIFACT_CONTRACT.md`, `requirements.m2.json`, `test-catalog.m2.json`, `STATE_MACHINES.m2.json`,
`EXCEPTION_CATALOG.m2.json`, `RED_SEQUENCE.m2.md`. M1 defect classes probed: `docs/m1_impl/CX_FINAL_CHECKLIST.md`.

## Ratified Architecture Decisions (SSOT)

1. **Host Boot/Discovery Timing:** Parse all discovered manifests at boot to populate a fast in-memory index; instantiate zero code for disabled extensions.
2. **Artifact GC (Garbage Collection):** Host boot sweeps uncommitted `.tmp` stage files older than 24 hours.
3. **Artifact Reference Deduplication:** Physical disk blobs deduplicated via SHA-256 CAS; Core event stream Records strictly preserve each peer's unique provenance.
4. **Extension Schema Downgrade:** Strictly forbidden. Host blocks the load if DB `schema_version` is newer than manifest, adhering to M1 strict Exit 6 policy (`DowngradeNotSupportedError`).
5. **Path Traversal & Sharding:** Enforce 2-tier directory hashing (`artifacts/<digest[:2]>/<digest>`) to circumvent OS flat-directory limits.
6. **Disabled Extension Passthrough:** When Artifact extension is OFF, the M1 bridge yields `m2.artifact.reference` as an opaque map, gracefully degrading without crash.
7. **Tamper Recovery:** Strictly fail reads immediately with `ArtifactTamperedError`; zero auto-repair or auto-quarantine logic to preserve deterministic immutability.
8. **Manifest Version Constraints:** Exact-version matches only. SemVer dependency solvers are explicitly non-goals for M2.0.


