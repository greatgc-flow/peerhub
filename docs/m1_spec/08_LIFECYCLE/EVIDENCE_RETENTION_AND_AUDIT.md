# Evidence Retention / Audit Guide

## Release evidence bundle

The following index is recommended for each release/candidate.

```text
evidence/<release-id>/
  release-evidence-index.json
  artifact-hashes.txt
  deterministic-tests.txt
  package-clean-install.txt
  live-canary.txt                # when applicable
  migration-restore-proof.txt    # when applicable
  known-risks.md
  observation-summary.md
  closure.md
```

Choose CI artifacts, release assets, or a separate evidence store as the actual storage location.

## Evidence principles

- Prefer immutable evidence or content-addressed hashes
- Record the command/environment/version/time together
- Minimize sensitive information
- Prefer terminal/CI/runtime output over self-reports
- Record changed requirement/test/catalog revisions together

## Retention period

Do not hardcode an arbitrary retention period in the package.
Set the `retention policy` based on organizational policy, regulations, and storage costs.
Retain the minimum evidence needed to reproduce the current stable release.

## Audit questions

- Why does this release exist?
- Which tests failed, and what fixed them?
- Was it verified on an actual provider?
- Is rollback possible?
- What are the known risks?
- Did it recur in operations?
- What lessons informed the next release?

## Gate evidence freshness / aging

Blocking gates accept only a candidate-bound **fresh PASS**. `QUEUED/RUNNING/PENDING/CANCELLED/STALE/UNAVAILABLE/UNKNOWN/SKIPPED` are not evidence. Revalidate relevant evidence when the provider CLI/model catalog/gate definition/candidate identity changes. Keep time thresholds in release/CI policy rather than hardcoding them in Core.

SSOT: `gate-evidence-policy.json`; schema: `gate-evidence-policy.schema.json`.
