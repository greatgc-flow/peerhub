# TDD / Release / Operations Integration Guide

## Test tiers

architecture → unit/property → schema → SQLite integration → concurrency/fault → E2E → real provider live → package/install.

## Release gate

Merely writing "live gate" in the documentation is insufficient.
The actual publish job in the CI DAG must depend on the success of that gate.

## Clean install

Install the wheel/sdist or final distribution artifact in a fresh environment, then smoke test it.

## Evidence

Retain terminal/CI/live output as evidence, not AI self-reports.

## Full M1 test set

Use `06_GUIDES/TEST_SET/README.md` as the SSOT entrypoint for specific RED-ready test designs. This includes Requirement→Test coverage, concurrency/fault injection, fake runtime E2E, real-provider gates, and package gates.

## Post-test

The lifecycle does not end when G5 publish is complete.

```text
G5 Publish
→ G7 invariant watch
→ stabilization observation
→ feedback/incident/drift triage
→ requirement/regression update
→ next RED
```

Use `08_LIFECYCLE/README.md` as the SSOT entrypoint for operations, rollback, incidents, evidence, and post-release reviews.
