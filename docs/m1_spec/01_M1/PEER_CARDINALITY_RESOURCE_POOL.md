# Peer Cardinality / Resource Pool

## Cardinality

```text
Adapter kind 1:N Peer
Peer 1:N Stream
Peer+Stream 1:N runtime sessions over time
Peer+Stream 0:1 active Bridge delivery owner
```

Do not hardcode per-vendor Peer limits in Core.

## Examples

```text
codex-cli -> cx-01 / cx-02 / cx-review
claude    -> cc-01 / cc-long
agy       -> ag-01 / ag-review
```

## Shared Resource Pool

Multiple Peers may share the same account quota/rate limit.

```text
cx-01 ─┐
cx-02 ─┼─> resource_pool:openai-account-X
cx-03 ─┘
```

Quota can be represented as `PeerObservation + resource_pool_ref` rather than a property of the Peer itself.

## Parallel Writes

Multiple Peers editing the same file concurrently in the same working tree is a separate issue.
M1 recommends separate git worktrees by default; a general lock manager belongs to a future Extension.
