# Release Gate Matrix

| Gate | Required suite | Network/quota | Blocks |
|---|---|---:|---|
| G0 Fast | architecture + schema + unit + property + meta + security-static | No | developer feedback |
| G1 Core | G0 + SQLite integration + concurrency + multiprocess + Core/storage fault + migration | No | Core merge |
| G2 M1 | G1 + Bridge + Observation + Diag + fake-runtime E2E + deterministic security | No | M1 merge / release candidate |
| G3 Live | advertised cc/cx/ag discovery/canary | Yes | publish |
| G4 Package | wheel/sdist + runtime-data contents + clean install + Python/OS matrix + restart smoke + consistency | No | publish |
| G5 Publish | G2 + G3 + G4 + evidence manifest/checksums | Mixed | artifact publication |
| G6 Soak | capacity/high-cardinality scheduled suite | No | non-blocking by default; becomes blocking only with ratified SLO |

## 핵심 규칙
- publish DAG가 G3/G4를 실제 dependency로 가져야 함.
- provider 인증/쿼터 부족은 성공으로 치지 않음.
- deterministic suite는 provider 장애와 독립 재현 가능.
- package metadata가 광고하는 Python/OS는 G4 matrix와 일치해야 함.
