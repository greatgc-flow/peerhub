# Operations / Observability / SLO Guide

## Observation layers

### A. Correctness invariants — zero tolerance

Monitor `invariants[]` in `closed-loop.json` / `INVARIANT_CATALOG.md` directly. Avoid drift by keeping no separate duplicate list in operational documentation.

### B. Reliability signals
- append commit success/failure category
- CAS conflict vs storage error
- Bridge delivery result: delivered / failed-before-start / may-have-started / terminal
- restart recovery success
- migration/restore success
- provider discovery/canary result

### C. Capacity/performance signals
- append/read latency percentiles
- pending/unread Record depth
- bridge catch-up depth/time
- DB busy/lock pressure
- observation staleness
- provider quota/rate headroom

### D. Product/operation signals
- repeated manual recovery
- false/ambiguous Diag output
- operator workaround frequency
- provider/model drift
- documentation/process mismatch

## Steps for setting SLOs

1. Collect a baseline first.
2. Distinguish user impact from business criticality.
3. Choose measurable SLIs.
4. Propose targets/windows as environment configuration.
5. Validate with canary/operational data.
6. Readjust thresholds if they are not valid.

## Preventing alert fatigue

- invariant breach: Immediate actionable alert
- transient provider/quota: Record as Observation without escalating to Core failure
- Repeated events with the same cause: aggregate into one Problem record
- Display stale/unknown as `UNKNOWN/STALE` without misrepresenting them as failures

## Personal/confidential information

Design operational evidence to exclude prompts/transcripts/credentials by default.
When needed, retain only the minimum scope separately and automate after the redaction policy is finalized.
