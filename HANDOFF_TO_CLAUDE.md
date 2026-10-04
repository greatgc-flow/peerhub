# Claude / Other AI Handoff Document: PeerHub M1 Very Simple Renewal

## 1. 개요 및 현재 상태
- **작업 디렉토리**: `D:\PkgDev\workspace\peerhub-m1-renewal` (Git worktree, 원본 `peerhub` 완전 보존)
- **현재 브랜치**: `feat/m1-very-simple-renewal` (깨끗한 working tree, 커밋 완료)
- **최신 커밋**:
  - `6469fc0`: `feat(m1): implement clean lightweight CLI entrypoint for Core and Diag`
  - `e192cd2`: `feat(m1): implement minimal core contract and 1st-party extensions with full tests`
- **테스트 상태**: `pytest tests\unit\m1` $\rightarrow$ **14/14 PASS**

---

## 2. 핵심 아키텍처 원칙 (반드시 준수)
1. **Core 4개 엔티티만 유지**: `Peer`, `Stream`, `Record`, `Offset`
   - 스펙: [docs/m1_spec/01_M1/CORE_CONTRACT.md](file:///D:/PkgDev/workspace/peerhub-m1-renewal/docs/m1_spec/01_M1/CORE_CONTRACT.md) 및 `04_SCHEMAS/*.schema.json` (JSON Schema Draft 2020-12 준수)
2. **Core 불변식**: `peerhub.m1` 코어는 어떠한 extension 모듈도 import하지 않음.
3. **1st-party 확장**:
   - `Observation` & `ReadonlyDiag` (`peerhub/extensions/observation_and_diag.py`)
   - `Session Bridge` (`peerhub/extensions/session_bridge.py`)
4. **Side-by-side vertical slice**: 기존 v0.x orchestrator/governance/routing 코드를 무리하게 삭제하지 않고 유지하면서 M1 Core를 깔끔하게 병렬 구축함.

---

## 3. 구현된 주요 파일 맵
- **코어 모델**: [`peerhub/m1/models.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/peerhub/m1/models.py)
  - `Peer`, `Stream`, `Record`, `Offset` (RFC 8785 서브셋 기반 SHA-256 payload digest 포함)
- **영속성 계층**: [`peerhub/m1/store.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/peerhub/m1/store.py)
  - SQLite WAL, Monotonic Position Append, Idempotency conflict 검증, Offset CAS(`advance_offset_cas`)
- **1st-party 확장**:
  - [`peerhub/extensions/observation_and_diag.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/peerhub/extensions/observation_and_diag.py): Read-time freshness 평가 (`MEASURED` $\rightarrow$ `STALE`), ReadonlyDiag (`mode=ro`)
  - [`peerhub/extensions/session_bridge.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/peerhub/extensions/session_bridge.py): `Single-active delivery claim` (세대 기반 브릿지 임대), Execution Certainty
- **CLI 엔트리포인트**: [`peerhub.m1_cli.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/peerhub/m1/cli.py)
  - `python -m peerhub.m1_cli [peer|stream|record|offset|diag]`
- **단위 테스트**:
  - [`tests/unit/m1/test_models.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/tests/unit/m1/test_models.py)
  - [`tests/unit/m1/test_store.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/tests/unit/m1/test_store.py)
  - [`tests/unit/m1/test_extensions.py`](file:///D:/PkgDev/workspace/peerhub-m1-renewal/tests/unit/m1/test_extensions.py)

---

## 4. 클로드에서 이어받을 다음 작업 (Recommended Next Steps)
1. **실제 CLI Provider 연결 (Claude / Codex / Agy Adapter)**:
   - `Session Bridge`의 `RuntimeTarget` 인터페이스를 통해 실제 로컬 CLI subprocess 프로세스 스폰 및 스트리밍 I/O 연결.
   - Claude CLI 인터럽트(`pause Record` 전달) 및 context boundary catch-up 처리.
2. **Acceptance Criteria 검증 ([docs/m1_spec/ACCEPTANCE_DOD.md](file:///D:/PkgDev/workspace/peerhub-m1-renewal/docs/m1_spec/ACCEPTANCE_DOD.md))**:
   - `same adapter kind N Peers` 동시 세션 테스트.
   - `session loss -> fresh catch-up` 시나리오 E2E 테스트.
3. **패키지 빌드 & 릴리즈 게이트 점검**:
   - `python docs\m1_spec\tools\validate_package.py` 지속적 패스 확인.

---

## Update 2026-10-04

Waves 0-9 are implemented (210/210 catalog ids). See `M1_PROGRESS_TRACKER.md`, `docs/m1_impl/PLAN.md`, `docs/m1_impl/DECISIONS.md` and `docs/m1_impl/OWNER_DECISIONS_NEEDED.md`. The CLI entrypoint moved to `peerhub/m1_cli.py` (Core never imports extensions). Real cc/cx/ag adapters live in `peerhub/extensions/adapters/`. Pending: cx.pro consolidated final review and the spec-owner decisions.
