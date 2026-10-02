# Peer Cardinality / Resource Pool

## Cardinality

```text
Adapter kind 1:N Peer
Peer 1:N Stream
Peer+Stream 1:N runtime sessions over time
Peer+Stream 0:1 active Bridge delivery owner
```

Core에는 Vendor별 최대 Peer 수를 hardcode하지 않습니다.

## 예

```text
codex-cli -> cx-01 / cx-02 / cx-review
claude    -> cc-01 / cc-long
agy       -> ag-01 / ag-review
```

## 공유 Resource Pool

여러 Peer가 같은 계정 quota/rate limit를 공유할 수 있습니다.

```text
cx-01 ─┐
cx-02 ─┼─> resource_pool:openai-account-X
cx-03 ─┘
```

Quota는 Peer 자체의 property가 아니라 `PeerObservation + resource_pool_ref`로 표현 가능합니다.

## 병렬 write

동일 working tree의 같은 파일을 여러 Peer가 동시에 수정하는 것은 별도 문제입니다.
M1 기본은 독립 git worktree 권장; 일반 lock manager는 미래 Extension으로 둡니다.
