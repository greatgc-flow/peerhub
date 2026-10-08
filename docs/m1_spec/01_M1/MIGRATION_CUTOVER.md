# Migration / Cutover

기존 v0.x orchestrator schema를 in-place 축소하지 않습니다.

```text
Existing PeerHub -> evidence/test/adapter mine
New M1 Core      -> side-by-side vertical slice
```

순서:
1. M1 contract/test freeze.
2. fresh minimal store.
3. generic proven utilities transplant.
4. fake Bridge.
5. real CC/CX/AG.
6. pause/resume/crash/catch-up.
7. cutover (2026-10-08: 단일 `peerhub` 진입점만 남음; v0 import/selector/disposition 표는 제거됨, 과거 v0는 Git 보존 브랜치로만 접근).
8. 이후 변경은 현재 Core schema 호환(MIG-001..003)만 보장합니다.
