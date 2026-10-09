# M2 Detailed Pre-TDD Review — 2026-10-05

We will keep the 7 slices of M2. We will not add an 8th slice.

## Actual Implementation Wave
`M2.0 minimal contract → Artifact → Work → Skill/Catalog → Eval/Telemetry → Backup/Recovery → MCP`

MCP will be attached as an external boundary after internal public ports are stabilized.

## M2.0 Minimal Extension Host
Required: manifest/registry/config validation/enable-disable/owned schema migration/lifecycle/failure isolation/public port binding.
PASS: marketplace, package installer, dependency solver, hot reload, third-party trust ecosystem(M4-J).
M1 static Modules are not retroactively converted to Extensions. Extension→Extension implementation imports are fundamentally prohibited and must be connected via public capability/ports. Failure isolation ≠ security sandbox.

## M2.1 Artifact
- metadata/blob authoritative, index/preview rebuildable.
- `artifact_id != digest`; digest is for content dedup/integrity.
- invariant: `stage → digest → verify → atomic blob commit → metadata → durable Record reference`.
- initial scope is single blob + optional manifest; directory-native artifact is a PASS.
- path traversal/symlink/reparse/disk-full/partial-copy/tamper/zero-byte/large/unicode are set as targets for RED.

## M2.2 Work
- `Record = authority`, `Work View = REBUILDABLE projection`.
- Minimal state: `OPEN → ACTIVE ↔ BLOCKED/PAUSED → DONE/FAILED/CANCELLED`.
- transition is `work_id + expected_revision`; an ordered Record reducer deterministically resolves races.
- Each Work has state-changing records in a single home Stream.
- The legacy claim/coordinator/failover/approval engine is not reused as is. We only ABSORB the lifecycle and defer scheduling/orchestration to M3.

## M2.3 Skill / Capability Catalog
Internal authority is separated: `Skill Source / Generated Skill Index / Capability Catalog / Selection Policy`.
- measured/volatile fact(quota, runtime version) → M1 Observation
- curated declared fact/capability → Catalog
- procedure/HOW → Skill
- activation/selection → Policy
Skill digest is based on the entirely normalized directory manifest. Script discovery ≠ execution authorization.

## M2.4 MCP
- Call public API/ports only, direct storage writes are prohibited.
- baseline is stdio-first. remote HTTP/auth will be expanded when evidence exists.
- PeerHub Work/Session state is not tied to the protocol connection state; an explicit handle/ref is passed on every request.
- MCP Tasks/Prompts are baseline N_A; add via adapter mapping if need is proven.

## M2.5 Backup / Recovery
Uses State Kind × Recovery Class. Order: `AUTHORITATIVE restore → hash/schema verify → generation fence → projection rebuild → external reconcile → invariant`.
Backup SUCCESS includes manifest/hash/reopen proof. Secrets are excluded by default, external refs explicitly state REFERENCE_ONLY/MIRRORED.

## M2.6 Eval / Telemetry
Execution Trace / Eval / Feedback Signal / Exporter are separated. Eval results are not truth. Datasets are treated as Artifacts, leaving evaluator type/version/evidence. OTel remains as an EXPORT_ONLY adapter/exporter.

## Freeze Invariants
1. Core remains Peer/Stream/Record/Offset.
2. Core imports Extension = 0.
3. M1 normal even with all M2 Extensions OFF.
4. Artifact immutable/digest-verifiable.
5. Blob durable before durable reference.
6. Work truth only ordered Records.
7. Work projection fully rebuildable.
8. Skill source != generated index != catalog != policy.
9. Volatile measured facts stay Observation.
10. MCP never writes storage directly.
11. Restore starts from authoritative and rebuilds derived.
12. Eval/Telemetry never becomes collaboration truth.
