# M2 Pre-TDD drafts (M2.0 Minimal Extension Host, M2.1 Artifact)

Status: **DRAFT for review, not frozen.** Drafted 2026-10-05 by ag.pro from the R9 roadmap package (09_ROADMAP/M2_*), then mechanically checked
(18 requirements / 60 tests, every requirement linked by >= 2 tests, required dimensions covered by the linked tests, no duplicate ids,
state-machine and exception-catalog test references all resolve). Not yet reviewed by a second model or the owner; the two contract documents
are shorter than the target depth and need expansion (crash matrix, ports, Windows rules) before the freeze.

Files: M2_0_EXTENSION_HOST_CONTRACT.md, M2_1_ARTIFACT_CONTRACT.md, requirements.m2.json, test-catalog.m2.json, STATE_MACHINES.m2.json,
EXCEPTION_CATALOG.m2.json, RED_SEQUENCE.m2.md. M1 defect classes to probe in every M2 review: docs/m1_impl/CX_FINAL_CHECKLIST.md.

## Open questions for the owner (ag.pro draft; recommended defaults in italics)

1. **Host Boot/Discovery Timing:** Should disabled extensions' manifests be parsed at boot to build a known catalog, or purely ignored until explicitly re-enabled? *(Recommended: Parse all discovered manifests at boot to populate a fast in-memory index, but instantiate zero code for disabled ones.)*
2. **Artifact GC (Garbage Collection):** If a staged blob fails to commit (e.g. crash), who cleans up the orphaned stage file and when? *(Recommended: Core bridge or a background cron deletes stage files older than 24h at host boot.)*
3. **Artifact Reference Deduplication:** If two peers commit the exact same blob independently, should they share the exact same `m2.artifact.reference` payload, or should provenance strictly differentiate the JSON record bodies while deduping the physical disk blob? *(Recommended: Physical blob deduplicated on disk; JSON Records strictly differentiate provenance.)*
4. **Extension Schema Downgrade:** If an extension is downgraded to an older version, should the host explicitly block the load due to future schema versions, or allow the extension to handle it? *(Recommended: Host blocks the load if `schema_version` is newer than the manifest, mirroring M1's strict exit 6 policy.)*
5. **Path Traversal Strictness:** For Artifact blobs, should we hash the digest into subdirectories (e.g., `da/39/da39a3e...`) immediately, or just use a flat folder until cardinality proves an issue? *(Recommended: Use `digest[:2]` hashing immediately to avoid OS flat-directory file limits.)*
6. **Unknown Kind Semantics:** When Artifact extension is OFF, M1 bridge encounters `m2.artifact.reference`. Should the bridge yield it as an opaque map, or filter it out? *(Recommended: Yield as an opaque dictionary; UI/consumer gracefully degrades to raw JSON view without crashing.)*
7. **Tamper Recovery:** If an artifact is detected as tampered, should the host auto-quarantine it, or just strictly fail reads? *(Recommended: Strictly fail reads with `ArtifactTamperedError`; no auto-repair or quarantine logic to keep the state machine simple.)*
8. **Manifest Version Constraints:** Do we require SemVer dependency resolution between extensions in M2.0? *(Recommended: No. Pass/Fail exact version matches only. Dependency solvers are explicitly non-goals for M2.0.)*


