# Observation + Readonly Diag

## Observation envelope

```text
observation_id
subject_ref
resource_pool_ref?
kind
source
observed_at
captured_at
state/freshness
payload
schema_version
```

M1 kind 후보:

```text
reachability
cli_version
runtime_capability
session
quota
rate_limit
activity
execution_failure
```

Quota와 rate limit는 구분합니다.

## Evidence vocabulary

```text
MEASURED
ABSENT
UNAVAILABLE
ERROR
STALE
UNKNOWN
```

관측되지 않은 값은 0/healthy/unlimited로 만들지 않습니다.
Freshness는 read 시 재평가합니다.

## Diag

Diag는 별도 readonly observer extension.

허용:
`read Stream/Record/Offset/Observation/log`.

금지:
`append/refresh/interrupt/resume/repair/restart/route`.

가능하면 SQLite `query_only/read-only` 경계를 사용합니다.
