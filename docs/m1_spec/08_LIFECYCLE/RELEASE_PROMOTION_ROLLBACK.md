# Release / Promotion / Rollback Guide

## Promotion ladder

```text
Developer GREEN
→ Release Candidate
→ Clean-install Smoke
→ Real-provider Canary
→ Preview / Limited Cohort (optional)
→ Stable
→ Stabilization Watch
```

Record the reason and approval evidence when skipping a stage.

## Required gates before Publish

`release-gates.json` is the SSOT for machine-readable gate dependencies; `06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json` is the SSOT for the primary gate mapping of 210 tests.

```text
G0 → G1 → G2 → G3 ┐
              └→ G4 ├→ G7 Invariant → G5 Publish
G2 → G6 Soak (non-blocking by default)
```

Use G0~G5 from `06_GUIDES/TEST_SET/RELEASE_GATE_MATRIX.md`.
The lifecycle also requires the following.

- Artifact revision matches the current requirement/test SSOT
- Release notes include known risks/deferred triggers
- Previous artifact/hash available for rollback
- Dry-run + backup/restore proof if migration is involved
- Successful actual canary if a live-provider is advertised
- Create a release evidence index

## Rollback / Hold trigger

Immediate rollback or publish hold:

- Core correctness invariant breach
- Verification mismatch for a destructive migration
- Repeated crash-loop or unrecoverable corruption in the new version
- A stale owner overwrites terminal evidence
- A read-only path performs mutation
- An advertised provider does not actually work at the release gate

For ordinary performance degradation, decide `hold / mitigate / continue` based on measured SLOs/thresholds.

## Rollback principles

1. Do not assume an automatic data schema downgrade.
2. Separate binary rollback from data rollback.
3. Preserve an authoritative state snapshot/evidence before rollback.
4. If the old version cannot read the data after migration, a forward-fix may be safer.
5. Keep the incident open after rollback. Continue through root cause analysis + regression + recurrence watch.

## Release channels are optional

Choose channels such as `preview`, `candidate`, and `stable` according to project scale.
Channel names are not part of the Core contract; manage them in config/CI pipelines.

## Evidence validity before promotion

`G5 publish` must verify that dependency gates have a fresh `PASS` for the same candidate identity, rather than merely checking that they exist. Cancelled/queued/stale canary results do not carry over as publish approval.
