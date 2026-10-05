# M2.0 & M2.1 RED Sequence

목표는 가장 위험한 불변식(Security, Core 침범, Data Loss)을 가장 먼저 증명하는 것입니다.

## Wave 0 — Isolation & Security Boundaries
- **EXT-001, EXT-002:** Core imports Extension = 0 검증 (의존성 격리).
- **ART-005..ART-008:** Artifact path traversal, symlink, unicode collision, reparse points 방어 실패 증명.
- **EXT-006, EXT-007:** Manifest validation & strict rejection.

## Wave 1 — Durable Invariants
- **ART-001, ART-002:** Blob durable before Core reference (ordering crash test).
- **ART-009, ART-010:** Tamper evidence & immutability bypass 방어 (INSERT OR REPLACE, bit flip).
- **EXT-003:** Schema isolation (Extension tables prefixed).

## Wave 2 — Fault Injection & Concurrency
- **ART-003, ART-004:** Disk-full, partial copy, zero-byte handling & atomic OS replace.
- **EXT-008:** Concurrent enable/disable toggle (transactional fencing).
- **ART-011:** Concurrent identical blob commits (idempotency, duplicate).

## Wave 3 — Lifecycle & Disable Continuity
- **EXT-004:** Disable preserves DB and allows M1 functionality.
- **EXT-009:** Rollback/Disable behavior ignores legacy hooks and unmapped states.
- **ART-012:** Disabled artifact extension ignores `m2.artifact.reference` safely without crashing stream read.
```

# OPEN QUESTIONS

1. **Host Boot/Discovery Timing:** Should disabled extensions' manifests be parsed at boot to build a known catalog, or purely ignored until explicitly re-enabled? *(Recommended: Parse all discovered manifests at boot to populate a fast in-memory index, but instantiate zero code for disabled ones.)*
2. **Artifact GC (Garbage Collection):** If a staged blob fails to commit (e.g. crash), who cleans up the orphaned stage file and when? *(Recommended: Core bridge or a background cron deletes stage files older than 24h at host boot.)*
3. **Artifact Reference Deduplication:** If two peers commit the exact same blob independently, should they share the exact same `m2.artifact.reference` payload, or should provenance strictly differentiate the JSON record bodies while deduping the physical disk blob? *(Recommended: Physical blob deduplicated on disk; JSON Records strictly differentiate provenance.)*
4. **Extension Schema Downgrade:** If an extension is downgraded to an older version, should the host explicitly block the load due to future schema versions, or allow the extension to handle it? *(Recommended: Host blocks the load if `schema_version` is newer than the manifest, mirroring M1's strict exit 6 policy.)*
5. **Path Traversal Strictness:** For Artifact blobs, should we hash the digest into subdirectories (e.g., `da/39/da39a3e...`) immediately, or just use a flat folder until cardinality proves an issue? *(Recommended: Use `digest[:2]` hashing immediately to avoid OS flat-directory file limits.)*
6. **Unknown Kind Semantics:** When Artifact extension is OFF, M1 bridge encounters `m2.artifact.reference`. Should the bridge yield it as an opaque map, or filter it out? *(Recommended: Yield as an opaque dictionary; UI/consumer gracefully degrades to raw JSON view without crashing.)*
7. **Tamper Recovery:** If an artifact is detected as tampered, should the host auto-quarantine it, or just strictly fail reads? *(Recommended: Strictly fail reads with `ArtifactTamperedError`; no auto-repair or quarantine logic to keep the state machine simple.)*
8. **Manifest Version Constraints:** Do we require SemVer dependency resolution between extensions in M2.0? *(Recommended: No. Pass/Fail exact version matches only. Dependency solvers are explicitly non-goals for M2.0.)*
