# PeerHub M1 Very Simple Renewal — FINAL

## M1 한 문장

> **PeerHub은 Peer를 식별하고 공유 Stream을 통해 Record를 신뢰성 있게 보존·전달하여,
> AI CLI의 프로세스·세션·컨텍스트·모델·쿼터가 바뀌어도 협업을 이어가게 하는
> 최소 durable communication layer다.**

Core는 **Peer / Stream / Record / Offset** 네 개뿐입니다.

M1 first-party extension:

```text
Session Bridge
Observation
Readonly Diag
```

그 외 기존 기능은 모두 `02_EXTENSIONS/`로 이관하여 N차 milestone에서 따로 논의합니다.

현행 repo evidence freeze: `greatgc-flow/peerhub` main HEAD `57a137cd6a0cc7e89124ea2b87b72995087627a5`, 기존 leaf command는 **109/109 disposition 완료**입니다.

## 시작 순서

1. `FINAL_M1_DECISION_KO.md`
2. `USAGE_GUIDE_KO.md`
3. `01_M1/CORE_CONTRACT.md`
4. `01_M1/LONG_HORIZON_CONTINUITY.md`
5. `01_M1/SESSION_BRIDGE.md`
6. `01_M1/OBSERVATION_AND_DIAG.md`
7. `01_M1/SKILLS_CATALOG_SECOND_BRAIN.md`
8. `02_EXTENSIONS/EXTENSION_CATALOG.md`
9. `03_STANDARDS/STANDARDS_DECISION_TABLE.md`
10. `06_GUIDES/TEST_SET/README.md`
11. `06_GUIDES/TEST_SET/RED_SEQUENCE.md`
12. `08_LIFECYCLE/README.md`
13. `08_LIFECYCLE/CLOSED_LOOP_OPERATING_MODEL.md`
14. `07_AUDIT/FINAL_RECURSIVE_AUDIT.md`

전역 `AGENTS.md`는 의도적으로 포함하지 않습니다.

테스트 설계 freeze: **88 requirements / 210 tests / recursive-MECE closure enforced**. 구현은 Wave 0 META/contract RED부터 시작합니다.

개발/테스트 이후 lifecycle도 freeze 대상입니다. 배포 이후에는 `08_LIFECYCLE/`의 release → observe → learn → improve → close → next intake 순환을 사용합니다.

## 종·횡 최종 교차점검 R2

- invariant SSOT 8/8 requirement 역추적
- test→release gate 210/210 machine mapping
- release gate DAG explicit/fail-closed
- signal route→lifecycle/runbook resolvability
- rollback 후 unresolved Problem follow-up closure 규칙
