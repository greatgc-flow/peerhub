# N차 Milestone 통폐합 기준

각 Extension family마다:

```text
KEEP
MERGE
SPLIT
ABSORB
SUPERSEDE
RETIRE
```

를 다시 결정합니다.

평가:
- 사용자 가치/빈도;
- M1 Core와의 직접성;
- 표준/OSS로 custom code 삭제 가능 여부;
- 실패 격리 가능성;
- provider specificity;
- quota/cost;
- authority/security;
- 실제 production evidence;
- 유지보수 부담.

현재 구현이 많다는 이유로 우선순위를 높이지 않습니다.
