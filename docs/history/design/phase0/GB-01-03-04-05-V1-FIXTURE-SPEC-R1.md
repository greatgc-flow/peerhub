# GB-01/03/04/05 V1 Fixture Spec R1

AG/CX reviewed provider-free broker fakes.

- GB-01: one CAS transaction commits target revision, pending receipt, and outbox row together or none.
- GB-03: same key/payload returns prior receipt without mutation; changed payload is `IDEMPOTENCY_PAYLOAD_MISMATCH`.
- GB-04: startup reconciles committed pending journal/outbox work without re-running the state transition or duplicating uncertain external effect.
- GB-05: CAS claim yields one immutable `EFFECT_SUCCEEDED` or `EFFECT_FAILED` receipt bound to request, outbox, attempt, and result; terminal completion cannot be overwritten.

All tests use fixed clock/IDs and an isolated journal/outbox; no provider or live authority state.
