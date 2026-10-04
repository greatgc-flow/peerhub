# Source Evidence Matrix

| M1 결정 | 기존/현행 근거 | 처리 |
|---|---|---|
| Stream > Session | GAP3 continuity 설계 | 원칙 유지, 모델 축소 |
| append + Offset | sqlite_events + tests | 패턴 재사용 |
| concurrency-safe ack | consumer offset CAS | 유지 |
| read-only Diag | read UoW tests | 재사용 |
| session generation | session lease/room session tests | Bridge로 축소 |
| uncertain execution | core.execution/retry | Bridge 의미론 유지 |
| cancellation | dispatch process/pipe | 구현 지식 재사용 |
| quota structure | telemetry contracts | Observation으로 이동 |
| read-time freshness | health/telemetry history | 유지 |
| model profile resolution | adapter/model config + 2026-10-01 model-profile manifest | facts/schema/test 패턴은 Catalog/Observation으로 ABSORB; selection policy는 declarative config로 분리 |
| model profile refresh procedure | current manifest `instructions`/`tier_rules` | mutable fact JSON에 혼합하지 않고 Agent Skill + policy로 SUPERSEDE |
| one-source consistency | `test_model_profiles_manifest.py` | Catalog↔config↔docs drift test 패턴 ABSORB |
| lifecycle safety | restore dry-run/`--apply`, reset safety snapshot | future Backup/Recovery extension evidence로 RETAIN |
| legacy command inventory | current production call-map @ `57a137cd...` | **109/109 disposition**, 0 unclassified |
| global prompt context injection | ask-context-injection design | M1에서 제거 |
| role/consensus/routing/task | 현행 domain services | future Extensions |
| current build health | GitHub Actions CI run `37027768347` | HEAD pyright + pytest success evidence |
