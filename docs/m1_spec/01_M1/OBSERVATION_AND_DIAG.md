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

Candidate M1 kinds:

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

Distinguish quota from rate limit.

## Evidence vocabulary

```text
MEASURED
ABSENT
UNAVAILABLE
ERROR
STALE
UNKNOWN
```

Do not represent unobserved values as 0/healthy/unlimited.
Reevaluate freshness at read time.

## Diag

Diag is a separate readonly observer extension.

Allowed:
`read Stream/Record/Offset/Observation/log`.

Prohibited:
`append/refresh/interrupt/resume/repair/restart/route`.

Use SQLite `query_only/read-only` boundaries where possible.
