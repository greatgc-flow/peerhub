# PeerHub M1 사용방법

이 문서는 목표 UX를 설명합니다. 기존 v0.x CLI 문법을 그대로 보장하는 문서가 아닙니다.

## 기본

```text
1. workspace 초기화
2. Peer 발견/등록
3. Stream 생성 및 members 지정
4. Record append
5. Session Bridge가 대상 Peer runtime에 전달
6. 응답을 Record로 append
7. Offset으로 catch-up/read 위치 관리
```

## 장기 작업

- **진행현황**: 먼저 Record/Observation/log에서 zero-token 조회.
- **잠깐 중단**: pause Record를 먼저 저장하고 Bridge가 best-effort interrupt.
- **재개**: 같은 provider session이 호환되면 resume, 아니면 fresh session + catch-up.
- **방향 수정**: 최종 목적이 같으면 같은 Stream에 redirect Record.
- **완전한 방향 전환**: old Stream close + new Stream + reference.
- **쿼터 소진**: Core에는 영향 없음. Observation만 갱신.
- **Peer 교체**: Stream 유지, 새 Peer가 Offset 이후 catch-up.

## 같은 CLI Peer 여러 개

Claude/Codex/Agy 각각 `0..N` Peer를 허용합니다.
동일 provider account의 quota/rate pool은 공유될 수 있으므로 Observation에서 resource pool로 표현합니다.

## 검증

```powershell
python .\tools\validate_package.py
```

결과는 화면과 `PACKAGE_VALIDATION.txt`에 동일하게 남습니다.

## 개발/테스트 이후

```text
개발 GREEN
→ package / clean install
→ real-provider canary
→ release
→ stabilization observation
→ feedback/incident/drift triage
→ regression/requirement update
→ 다음 RED
```

운영 절차와 rollback/incident/evidence/post-release review는 `08_LIFECYCLE/README.md`에서 시작합니다.

## 종·횡 최종 교차점검 R2

- invariant SSOT 8/8 requirement 역추적
- test→release gate 210/210 machine mapping
- release gate DAG explicit/fail-closed
- signal route→lifecycle/runbook resolvability
- rollback 후 unresolved Problem follow-up closure 규칙
