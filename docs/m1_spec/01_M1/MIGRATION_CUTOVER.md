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
7. explicit importer for 꼭 필요한 data만.
8. compatibility aliases 판단.
9. cutover.
10. old data retention 별도 결정.

기존 기능은 삭제 전 109/109 disposition을 유지합니다.
