# CJ-02/05 V1 Fixture Spec R1

CJ-02 uses a fixed valid mutating envelope: client/actor identity, method,
client request and idempotency keys, workspace scope, expected policy and
configuration revisions. Admission mints a command ID while preserving those
identity fields; no provider call is needed.

CJ-05 changes only the actor to an unauthorized identity. It returns
`ACTOR_UNAUTHORIZED` at admission with exit 3, `NOT_STARTED` certainty and
never retry. Command ID remains null and no state, receipt, outbox, provider,
or dispatch side effect occurs.
