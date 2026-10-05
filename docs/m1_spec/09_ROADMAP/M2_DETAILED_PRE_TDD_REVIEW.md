# M2 Detailed Pre-TDD Review — 2026-10-05

M2 7개 slice는 유지합니다. 8번째 slice를 추가하지 않습니다.

## 실제 구현 Wave
`M2.0 minimal contract → Artifact → Work → Skill/Catalog → Eval/Telemetry → Backup/Recovery → MCP`

MCP는 내부 public ports가 안정된 뒤 외부 boundary로 붙입니다.

## M2.0 Minimal Extension Host
필수: manifest/registry/config validation/enable-disable/owned schema migration/lifecycle/failure isolation/public port binding.
PASS: marketplace, package installer, dependency solver, hot reload, third-party trust ecosystem(M4-J).
M1 static Modules는 소급 Extension화하지 않습니다. Extension→Extension 구현 import는 기본 금지하고 public capability/port로 연결합니다. Failure isolation ≠ security sandbox.

## M2.1 Artifact
- metadata/blob authoritative, index/preview rebuildable.
- `artifact_id != digest`; digest는 content dedup/integrity.
- invariant: `stage → digest → verify → atomic blob commit → metadata → durable Record reference`.
- initial scope는 single blob + optional manifest; directory-native artifact는 PASS.
- path traversal/symlink/reparse/disk-full/partial-copy/tamper/zero-byte/large/unicode를 RED 대상으로 둡니다.

## M2.2 Work
- `Record = authority`, `Work View = REBUILDABLE projection`.
- 최소 상태: `OPEN → ACTIVE ↔ BLOCKED/PAUSED → DONE/FAILED/CANCELLED`.
- transition은 `work_id + expected_revision`; ordered Record reducer가 race를 deterministic하게 판정합니다.
- 각 Work는 하나의 home Stream에서 state-changing record를 갖습니다.
- legacy claim/coordinator/failover/approval engine은 그대로 재사용하지 않습니다. lifecycle만 ABSORB하고 scheduling/orchestration은 M3로 보냅니다.

## M2.3 Skill / Capability Catalog
내부 authority를 분리합니다: `Skill Source / Generated Skill Index / Capability Catalog / Selection Policy`.
- measured/volatile fact(quota, runtime version) → M1 Observation
- curated declared fact/capability → Catalog
- procedure/HOW → Skill
- activation/selection → Policy
Skill digest는 전체 normalized directory manifest 기준. Script discovery ≠ execution authorization.

## M2.4 MCP
- public API/port만 호출, storage direct write 금지.
- baseline은 stdio-first. remote HTTP/auth는 evidence가 있을 때 확장.
- PeerHub Work/Session state를 protocol connection state에 묶지 않고 explicit handle/ref를 매 요청에 전달.
- MCP Tasks/Prompts는 baseline N_A; 필요가 증명되면 adapter mapping으로 추가.

## M2.5 Backup / Recovery
State Kind × Recovery Class를 사용합니다. `AUTHORITATIVE restore → hash/schema verify → generation fence → projection rebuild → external reconcile → invariant` 순서.
Backup SUCCESS는 manifest/hash/reopen proof까지 포함합니다. Secret은 기본 제외, external ref는 REFERENCE_ONLY/MIRRORED를 명시합니다.

## M2.6 Eval / Telemetry
Execution Trace / Eval / Feedback Signal / Exporter를 분리합니다. Eval 결과는 truth가 아닙니다. Dataset은 Artifact로 두며 evaluator type/version/evidence를 남깁니다. OTel은 EXPORT_ONLY adapter/exporter로 유지합니다.

## Freeze Invariants
1. Core remains Peer/Stream/Record/Offset.
2. Core imports Extension = 0.
3. 모든 M2 Extension OFF에서도 M1 정상.
4. Artifact immutable/digest-verifiable.
5. Blob durable before durable reference.
6. Work truth only ordered Records.
7. Work projection fully rebuildable.
8. Skill source != generated index != catalog != policy.
9. Volatile measured facts stay Observation.
10. MCP never writes storage directly.
11. Restore starts from authoritative and rebuilds derived.
12. Eval/Telemetry never becomes collaboration truth.
