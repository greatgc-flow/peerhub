# Closed-loop Operating Model

## 1. Principles

1. **Evidence first** — Prioritize reproducible Records, CI output, package hashes, and live canary results over opinions.
2. **Invariant first** — Protect data/delivery correctness invariants before performance targets.
3. **No silent repair** — Do not apply arbitrary automatic recovery to corruption, uncertain execution, or migration ambiguity.
4. **Every signal is classified** — Close each signal with one of `FIX / ROLLBACK / MITIGATE / ACCEPT / DEFER / INVALID / DUPLICATE` rather than ignoring it.
5. **Every fix returns to RED** — For defects found in operations, add a reproducing test first where possible.
6. **No-code / declarative first** — Keep thresholds, rollout, and catalogs in config/JSON rather than hardcoding them in the implementation.
7. **Core stays small** — Do not immediately add Core objects when an operational need arises. First consider whether Extension/Skill/Catalog can accommodate it.

## 2. lifecycle

| Stage | Key question | Required output | On failure |
|---|---|---|---|
| L0 Intake | Why change? | evidence + requirement/decision | classify/defer |
| L1 RED | Has the failure been reproduced? | failing test/eval or reason testing is not possible | strengthen evidence |
| L2 Build | Is this the smallest change? | implementation + trace | L1 |
| L3 Verify | Are deterministic gates green? | test evidence | L2 |
| L4 Package | Does a fresh install behave the same? | artifact + hash + clean smoke | L2/L3 |
| L5 Canary | Is it verified on the actual CLI/provider? | live evidence | hold/rollback |
| L6 Release | Is release evidence complete? | release evidence index | rollback/hold |
| L7 Observe | Do invariants hold in actual use? | stabilization observation | incident/triage |
| L8 Learn | Have the signal's cause and type been classified? | feedback/problem record | reproduce |
| L9 Improve | Were requirements, tests, and docs/catalog updated together? | linked change set | L1 |
| L10 Close | Is recurrence watch complete? | closure evidence | L7/L8 |

## 3. Invariants requiring an immediate stop

The SSOT for the exact list and requirement links is `invariants[]` in `closed-loop.json`; `INVARIANT_CATALOG.md` is the human-readable view.

These invariants are a **zero-tolerance correctness contract**, not numerical SLOs. If any breach is observed, stop the release/canary and take the incident/rollback/hold route specified by its `on_breach`.

## 4. Performance/capacity targets are configuration values

Do not fix targets for latency, backlog, observation staleness, uncertainty rate, and similar measures in package documentation.

```text
baseline observation
→ environment-specific threshold candidate
→ ratify
→ config/catalog
→ alert / release gate
→ periodic recalibration
```

Do not declare unmeasured numbers as SLOs.

## 5. Closure conditions for a change

A change is `Closed` only when all of the following are true.

- source evidence/requirement is linked
- a regression or conformance test is linked; if impossible, the reason is recorded
- deterministic gates passed
- real-provider/live gates passed where applicable
- artifact/hash/clean-install evidence exists
- operational signals have terminal dispositions
- docs/catalog/schema impacts are addressed or an N/A rationale exists
- the need for rollback/mitigation is decided
- recurrence watch results are recorded
