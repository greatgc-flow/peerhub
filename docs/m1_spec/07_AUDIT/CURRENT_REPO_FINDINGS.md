# 현재 PeerHub Repository 주요 발견

검토 기준: `greatgc-flow/peerhub` main, HEAD `57a137cd6a0cc7e89124ea2b87b72995087627a5` (2026-10-02 commit, 2026-10-03 재검증).
이 패키지의 이전 기준선은 `39b8949336dae039fc32c92dee55edf69b479b0d` (2026-09-30)입니다.

## 기준선 대비 Delta

- 현재 main은 이전 기준선보다 **6 commits ahead / 0 behind**.
- compare 결과 변경 파일은 **18개**.
- 현재 HEAD의 GitHub Actions `CI` run `37027768347`은 **success**.
  - `Run pyright` success.
  - `Run pytest` success.
- 현재 source version은 v0.10.1 계열이며 model-profile refresh와 call-map 동기화가 반영됨.

## 중요 교정: 107 -> 109

현재 `docs/design/peerhub-production-call-map-R1.json`은 **109 leaf commands**를 기록합니다.
기존 패키지의 107/107 완전성 주장은 현행 repo 기준으로 불완전했습니다.
후속 call-map 동기화에서 다음 두 v0.10.0 command가 빠져 있었음이 확인되었습니다.

- `workspace reset` — `MUTATING_EXTERNAL` -> **FUTURE_EXTENSION / Backup/Recovery / N**.
- `backup global` — `MUTATING_EXTERNAL` -> **FUTURE_EXTENSION / Backup/Recovery / N**.

따라서 본 revision은 `02_EXTENSIONS/109_COMMAND_DISPOSITION.*`로 109/109를 다시 닫았습니다.
두 기능은 fail-safe lifecycle/backup 패턴으로는 유용하지만 **Peer/Stream/Record/Offset Core에는 들어가지 않습니다.**

## Model profile 변화

현행 repo에는 `docs/model-profiles/model-profiles.json` + JSON Schema + consistency test가 추가되었습니다.
이는 다음 M1 원칙을 강화하는 evidence입니다.

- 변동 사실/모델 ID/CLI version -> structured Catalog.
- 구조 -> JSON Schema.
- runtime config/test/doc expectation -> One Source Multi Use.

다만 현행 manifest는 모델 사실과 `tier_rules`, refresh `instructions`를 같은 JSON에 포함합니다.
M1 renewal에서는 이를 그대로 복제하지 않고 다음처럼 더 엄격히 분리합니다.

```text
WHAT / observed changing facts -> JSON Catalog
SHAPE                         -> JSON Schema
HOW / refresh & verification -> Agent Skill
selection/binding policy      -> declarative policy/config
```

즉 현행 repo의 **facts/schema/consistency-test 패턴은 ABSORB**, procedure/policy 혼합은 **SUPERSEDE**합니다.

## Backup/restore 변화

- restore는 dry-run default + 명시적 `--apply`로 정리됨.
- `workspace reset`은 pre-reset safety snapshot을 둔 fail-closed 흐름.
- `backup global`이 추가됨.

이들은 M1 Core 확대 근거가 아니라 **future Backup/Recovery extension**의 좋은 구현 evidence입니다.

## 재사용할 시행착오/자산

- SQLite WAL/UoW/read-only.
- event append + consumer offset/CAS.
- session generation/fingerprint.
- ExecutionCertainty.
- Windows binary/process-tree handling.
- structured quota/usage/freshness.
- real Claude/Codex/Agy live tests.
- online SQLite backup 및 fail-safe lifecycle 경험.
- model profile manifest + schema + drift test 패턴.

## 그대로 가져오지 않을 것

- Room application-sequence race.
- orchestration/governance/task/routing을 Core화한 구조.
- diag의 collect/persist/render 혼합.
- global directive/lesson prompt injection.
- generic lease/capability platform.
- mutable fact catalog 안에 operational procedure를 같이 넣는 구조.

## 여전히 보이는 drift

- README의 stable-version/adapter-version 문구 일부가 최신 v0.10.1/model manifest와 완전히 동기화되지 않음.
- `docs/STATUS.md`의 상단 재검증 날짜와 이후 release history 시점이 다름.
- 문서/상태/source를 여러 위치에 반복 하드코딩하면 drift가 재발할 수 있음.

M1에서는 canonical structured source에서 runtime/test/doc projection을 만들고, 새 최소 계약을 타입/DB/test와 1:1로 맞춰 이러한 drift를 상속하지 않습니다.

## 결론

현재 repo delta는 **M1 아키텍처 결정을 뒤집지 않습니다.**
오히려 109 command completeness 교정과 model-profile SSOT 경험은 `Very Simple + One Source Multi Use + Skill/Catalog separation`을 강화하는 추가 evidence입니다.
