# Evidence Retention / Audit Guide

## Release evidence bundle

각 release/candidate마다 다음 인덱스를 권장합니다.

```text
evidence/<release-id>/
  release-evidence-index.json
  artifact-hashes.txt
  deterministic-tests.txt
  package-clean-install.txt
  live-canary.txt                # applicable할 때
  migration-restore-proof.txt    # applicable할 때
  known-risks.md
  observation-summary.md
  closure.md
```

실제 저장 위치는 CI artifact, release asset, 별도 evidence store 중 선택 가능합니다.

## 증적 원칙

- immutable 또는 content-addressed hash를 우선
- 실행 명령/환경/버전/시간을 같이 기록
- 민감정보는 최소화
- self-report보다 terminal/CI/runtime output 우선
- 변경된 requirement/test/catalog revision을 같이 기록

## 보존 기간

패키지에 임의 기간을 하드코딩하지 않습니다.
조직 정책·법규·storage 비용을 기준으로 `retention policy`를 설정합니다.
단, 현재 stable release를 재현하는 데 필요한 최소 evidence는 삭제하지 않습니다.

## 감사 질문

- 이 release가 왜 존재하는가?
- 어떤 테스트가 실패했고 무엇이 고쳤는가?
- 실제 provider에서 확인했는가?
- rollback 가능한가?
- known risk가 무엇인가?
- 운영에서 다시 발생했는가?
- 다음 release에 어떤 학습이 들어갔는가?
