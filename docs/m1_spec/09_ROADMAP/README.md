# PeerHub Master Roadmap

이 디렉터리는 **M1 → M2 → M3 → Optional Capability Tracks**의 단일 SSOT/가이드입니다.

핵심 원칙:

```text
M1 Durable Communication
  ↓
M2 Durable Work Continuity
  ↓
M3 Federated Intelligent Collaboration
  ↓
Required roadmap complete
  ↓
Optional Capability Tracks (M4-A ... M4-L)
```

- M1~M3는 기본 로드맵입니다.
- M4 이후는 순차적 의무 milestone이 아니라 **독립 opt-in capability track**입니다.
- Core는 끝까지 `Peer / Stream / Record / Offset` 네 개입니다.
- M2/M3/Optional 기능은 Core로 역유입하지 않습니다.
- machine-readable SSOT는 `roadmap.json`입니다.
- `roadmap.schema.json`과 package validator가 구조/경계를 검증합니다.

## 반드시 기억할 위치

기존 `peerhub diag`의 **quota / rate-limit / runtime 상태 조회는 M1**입니다.

```text
M1 Observation -> quota/rate_limit evidence 수집
M1 Diag        -> 해당 evidence를 strict read-only로 조회/표시
M3 Routing     -> quota evidence를 선택 정책에 사용
M4-B           -> 여러 작업 사이 quota/resource를 실제 배분/조정
```

따라서 쿼터 조회를 위해 M2/M3를 기다리지 않습니다.
