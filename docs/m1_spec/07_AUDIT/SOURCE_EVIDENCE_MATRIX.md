# Source Evidence Matrix

| M1 decision | Existing/current evidence | Disposition |
|---|---|---|
| Stream > Session | GAP3 continuity design | Retain the principle, simplify the model |
| append + Offset | sqlite_events + tests | Reuse the pattern |
| concurrency-safe ack | consumer offset CAS | Retain |
| read-only Diag | read UoW tests | Reuse |
| session generation | session lease/room session tests | Reduce to Bridge |
| uncertain execution | core.execution/retry | Retain Bridge semantics |
| cancellation | dispatch process/pipe | Reuse implementation knowledge |
| quota structure | telemetry contracts | Move to Observation |
| read-time freshness | health/telemetry history | Retain |
| model profile resolution | adapter/model config + 2026-10-01 model-profile manifest | ABSORB facts/schema/test patterns into Catalog/Observation; separate selection policy into declarative config |
| model profile refresh procedure | current manifest `instructions`/`tier_rules` | SUPERSEDE with Agent Skill + policy, keeping them separate from mutable fact JSON |
| one-source consistency | `test_model_profiles_manifest.py` | ABSORB the Catalog↔config↔docs drift test pattern |
| lifecycle safety | restore dry-run/`--apply`, reset safety snapshot | RETAIN as evidence for a future Backup/Recovery extension |
| legacy v0 command inventory | frozen call-map @ `57a137cd...` | **109/109 disposition**, 0 unclassified; migration evidence, not complete current installed CLI surface |
| global prompt context injection | ask-context-injection design | Remove from M1 |
| role/consensus/routing/task | Current domain services | future Extensions |
| current build health | GitHub Actions CI run `37027768347` | HEAD pyright + pytest success evidence |

| current M1 implementation | main `4a6994e7...` | waves 0-9 implemented; `peerhub-m1` side-by-side; exact current-head CI/live/package/invariant/release-evidence gates PASS |
| current CLI cutover | `pyproject.toml`, `peerhub/__main__.py`, `peerhub/m1_cli.py` | legacy `peerhub` default + 11-leaf `peerhub-m1`; quota Diag parity required before cutover |
