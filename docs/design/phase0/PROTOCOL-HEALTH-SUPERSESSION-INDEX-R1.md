# Protocol and Health Supersession Index R1

## Frozen protocol crosswalk

- changed payload under an existing idempotency identity: `IDEMPOTENCY_PAYLOAD_MISMATCH`;
- post-spawn execution deadline: `PROCESS_TIMEOUT` (silence is `SILENCE_TIMEOUT`);
- framing order: complete UTF-8 JSONL frame, JSON parse, protocol-major check,
  then schema-version check.

No prior frozen hash is edited. A replacement ratification round needs UUID,
date, frozen electorate, dependency SHA-256 values, and a canonical UTF-8 hash
policy for the newly ratified body.

## Health supersession

R3 controls receipt trust (host mint/broker journal/CAS), generic-500 scope
(profile only unless family evidence exists), recovery authority (automatic
health circuit only), and RuntimeRevision. RuntimeRevision includes effective
sandbox/elevation and stable execution facts; elevated proof does not prove an
unelevated production revision. Empirical legacy verification remains evidence,
not normative recovery authority.
