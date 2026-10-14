# Observation + Readonly Diag

## Physical storage and diagnostic projections

`observations` remains the immutable evidence table. `ObservationStore` also
maintains `observation_rollups` (latest capture and exact row count per subject,
kind, and pool identity) and `activity_rollups` (the newest 50 activity capture
sequences per subject). SQLite insert triggers update these projections in the
same transaction as evidence append. A first write against a legacy store
backfills missing projections transactionally; read-only diagnostics never
create or repair them and use the historical-scan compatibility path when the
tables are absent. `peerhub diag integrity` checks projection parity as well as
SQLite page integrity. The projections are derived data, not additional public
evidence or Core entities.

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
