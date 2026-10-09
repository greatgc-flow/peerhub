# Migration / Cutover

Do not reduce the existing v0.x orchestrator schema in place.

```text
Existing PeerHub -> evidence/test/adapter mine
New M1 Core      -> side-by-side vertical slice
```

Sequence:
1. M1 contract/test freeze.
2. fresh minimal store.
3. generic proven utilities transplant.
4. fake Bridge.
5. real CC/CX/AG.
6. pause/resume/crash/catch-up.
7. cutover (2026-10-08: only the single `peerhub` entry point remains; v0 import/selector/disposition tables have been removed, and historical v0 is accessible only through a preserved Git branch).
8. Subsequent changes guarantee only compatibility with the current Core schema (MIG-001..003).
