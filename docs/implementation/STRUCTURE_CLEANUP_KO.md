# 제품 구조 정리와 v0 retirement
> **2026-10-08:** `core.legacy_import`, 그 frozen fixture와 importer 테스트, `PEERHUB_CLI` selector는 모두 제거되었다. 단일 `peerhub` CLI만 남고, v0 소스는 Git 브랜치 `legacy/v0-main-final`에서만 접근한다. 아래 서술은 당시 기록이다.


## 판단 기준

M1/M2/M3는 개발 milestone이다. 런타임 책임·모듈 경계·CLI 이름에 남길 필요는 없다. 반면 검증된 frozen spec, 과거 evidence, durable storage/wire 식별자는 이름 자체가 계약 또는 출처이므로 보존한다.

| 이전 | 현재 | 이유 |
|---|---|---|
| `peerhub/m1/` | `peerhub/core/` | 네 가지 durable communication primitive의 실제 책임 |
| `peerhub/m2/*.py`, `peerhub/m3/*.py` | `peerhub/extensions/<capability>.py` | 기능을 Core 밖에 격리; milestone별 독립 엔진을 만들지 않음 |
| `peerhub/m1_cli.py` | `peerhub/cli/app.py` | 제품 CLI 구현 |
| `peerhub-m1` console script | `peerhub` 하나 | 임시 side-by-side 진입점 retirement |
| `tests/m1`, `tests/m2`, `tests/m3` | `tests/communication`, `tests/continuity`, `tests/collaboration` | 기능별 검증 범위 |
| `tests/unit/m1` | `tests/communication/unit` | 실제 Core 단위 테스트는 보존 |
| `tools/m1_*` | `command_inventory.py`, `traceability.py`, `release_evidence.py` | 현재 개발 도구의 역할을 이름에 표시 |
| 현재 command inventory / 구현 안내 | `docs/implementation/` | 과거 milestone evidence와 현재 설명을 분리 |
| pytest `m1_id` marker | `catalog_id` | frozen requirement ID의 연결은 그대로 유지 |

## AG와의 협업

AGY를 plan/read-only 모드로 독립 검토에 사용했다. 검토 conversation은 `d1b9a63b-8987-412a-bd70-794617783942`다. AG는 기존 v0 `core`와 이름 충돌, 동적 CLI proxy, importer fixture의 v0 persistence 의존성, DB discovery의 데이터 유실 위험, package-data·workflow 경로 갱신, frozen evidence 보존을 지적했다. 이를 실제 변경에 반영했다. 첫 검토가 제한 시간을 넘겨 도구 사용을 중단하고 기존 관찰만 요약하도록 재요청했다. AG는 파일을 수정하지 않았다.

## 제거한 v0 코드

v0의 adapters / application / builtins / old core / dispatch / events / governance / health / persistence / old routing / state / telemetry, client/runtime facade, CLI commands/parser/context/monolith를 제품 경로에서 제거했다. 현재 Core는 새 communication 구현이다.

v0 전용 unit/integration/contract/e2e/static 테스트와 old fixture captures, phase0 / consensus / legacy facade retirement / manifest/facts 도구도 제거했다. `tests/unit/m1`의 실제 Core 테스트는 옮겨 보존했다.

삭제는 로컬 복구 사본을 `.peerhub/retired-v0-a6c6964ed6f74daf990da36388d9968a/`에 두는 방식으로 진행했다. Git의 영구 복구 기준은 `legacy/v0-main-final` / `57a137cd6a0cc7e89124ea2b87b72995087627a5`다. 사용자 DB·관측·Record는 삭제하지 않았다.

## 의도적으로 남긴 식별자

- `docs/m1_spec`, `docs/m2`, `docs/m3`: frozen 계약과 catalog의 출처.
- `docs/m1_impl`의 과거 CI/live/matrix evidence와 wave/gate policy: 기존 검증 근거의 checksum·revision을 보존한다. 이번 코드의 PASS 증거로 간주하지 않는다.
- schema `$id`, 기존 `m2_*` table / `m2.*` Record kind, importer JSON의 `m1` conflict 필드 등: persisted data·wire 및 승인한 plan digest의 compatibility 계약이다. 단순 네이밍 정리를 이유로 rewrite하지 않는다.
- 기존 `.peerhub/m1.db`: read-only discovery fallback에만 남긴다. 새 workspace는 `.peerhub/core.db`; 가까운 workspace를 먼저 선택하고, 같은 workspace에 두 파일이 있으면 current 이름을 선택한다. 명시한 `--db`는 fallback하지 않는다.

## 검증 범위와 release

CLI inventory, schema package-data, import graph, 현재 workflow 경로와 테스트 selection을 함께 갱신했다. 삭제된 legacy slow/e2e gate를 빈 test selection으로 통과시키지 않도록 실제 quota collection과 public ask/retry canary를 새 live 테스트로 대체했다. provider 테스트는 `PEERHUB_LIVE=1`로 opt-in한다. 현재 release gating의 G0–G7 ID·frozen requirement IDs는 유지한다.

로컬 기본 스위트 1,178개를 실행했다: 첫 실행 1,160 PASS / 15 SKIP / 경로 관련 3 FAIL. 이미 로드된 이전 inventory 경로를 보던 2개와 Core 단위 테스트의 마지막 `from peerhub import m1` 참조를 정리한 뒤 `pytest --lf`에서 3개 모두 PASS로 재검증했다. 전체를 다시 실행한 단일 green JUnit으로 포장하지 않고 최초 결과와 재검증 결과를 각각 `.peerhub/cleanup-junit.xml`, `.peerhub/cleanup-recheck-junit.xml`에 보존했다. SKIP은 타 OS/Python CI cell 14개와 Windows symlink 권한 1개다. 실제 provider/soak 14개는 기본 실행에서 deselect됐다.

Pyright 전체 0 errors; parser-derived inventory check와 Wave 0–9 frozen catalog traceability PASS. clean source의 wheel/sdist build·격리 설치·schema bytes·Windows path/newline package 테스트도 검증했다. 별도 package 실행의 과거 evidence 인용 1개 실패는 해당 기록의 snapshot/head 범위를 명시해 두 항목 모두 PASS로 재검증했다. 기존 `.peerhub/m1.db`에서 설치된 `peerhub diag quota`가 관측을 읽었고, 이전 실제 Codex 요청을 같은 ID로 호출해 네트워크 재실행 없이 `recovered_terminal`을 확인했다.

기존 release evidence를 재사용해 새 구조의 VERIFIED를 주장하지 않으며 version/tag/push/publishing은 수행하지 않는다. 현재 source의 편집 설치는 갱신해 임시 `peerhub-m1` script를 제거했다. 옛 incremental build cache도 로컬 retirement 사본으로 옮겼으며 새 wheel에는 retired package가 포함되지 않는다.
