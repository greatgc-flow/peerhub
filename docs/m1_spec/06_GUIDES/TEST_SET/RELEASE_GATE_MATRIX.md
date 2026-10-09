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

## Core Rules
- publish DAG must have G3/G4 as actual dependencies.
- provider auth/quota failures do not count as success.
- deterministic suites are reproducible independently of provider failures.
- Python/OS advertised by package metadata must match the G4 matrix.
