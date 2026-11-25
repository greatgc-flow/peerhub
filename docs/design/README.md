# Design Documents Index

[Development status](../STATUS.md) tracks the current queue and the triggers for reopening deferred work.

## Current decisions and contracts

- **Active** — [Monitor pace health and caller ownership](monitor-pace-health-2026-11-23.md) records the decisions behind the [monitor guide](../monitor.md).
- **Active** — [Inbound A2A server](a2a-inbound-server.md) records the deferred proposal and the conditions for implementation referenced by the [remote ask guide](../remote-ask.md).
- **Active** — [Per-peer prompt-cache TTL](backlog-cache-ttl-2026-11-20.md) records the measurements and start trigger for the backlog item.
- **Active** — [Retained session contract](../implementation/RETAINED_SESSIONS.md) covers the registry, retention, replay, and the `--session` and `--fresh` selectors.

## Historical records

- **Historical** — Release plans for [0.24.0](../history/design/plan-0.24.0-2026-11-08.md), [0.24.1](../history/design/plan-0.24.1-2026-11-08.md), [0.24.4](../history/design/plan-0.24.4-2026-11-09.md), [0.24.5–0.24.6](../history/design/plan-0.24.5-2026-11-10.md), [0.25.0](../history/design/plan-0.25.0-2026-11-20.md), and [0.26.0](../history/design/plan-0.26.0-2026-11-21.md).
- **Historical** — The [pre-TDD renewal](../history/design/peerhub-pre-tdd-renewal-2026-10-14.md), [deepthink debate ledger](../history/design/peerhub-deepthink-debate-2026-10-14.md), [CLI and monitoring addendum](../history/design/peerhub-cli-monitoring-inspection-2026-10-14.md), and [session reuse analysis](../history/design/analysis-session-reuse-as-is-2026-10-13.md) record the October architecture review.
- **Historical** — The [monitor peers view](../history/design/monitor-peers-view-2026-10-16.md) records the original layout and deferred proposals for [expanded details](../history/design/monitor-expand-details-2026-10-16.md), [lineage](../history/design/monitor-e2e-lineage-2026-10-16.md), [live evidence](../history/design/live-evidence-archive-2026-10-16.md), and [schema v2](../history/design/db-schema-v2-2026-10-16.md).
- **Historical** — The [blocked streams plan](../history/design/plan-stuck-streams-2026-10-12.md), [quota forecast plan](../history/design/plan-quota-forecast-2026-10-12.md), and [October issue resolution note](../history/design/issue-resolution-2026-10-18.md) record shipped slices and deferred proposals. See the [backlog](../STATUS.md#deferred-backlog-start-triggers) for current triggers.
- **Historical** — The [peer CLI audit](../history/design/peer-cli-option-audit-2026-10-15.md), [model pools and guide design](../history/design/pools-guide-2026-10-17.md), and [profile consolidation proposal](../history/design/profile-ssot-2026-10-29.md) record earlier adapter and model work.
- **Historical** — The [consensus activation draft](../history/design/CONSENSUS-ACTIVATION-RUNBOOK.md) and [REL-005 pytest oracle](../history/design/rel005-pytest-oracle-2026-10-16.md) record earlier operational proposals.
- **Historical** — The [2026-11-22 scoring record](../history/design/scoring-2026-11-22/README.md) preserves the rubric, protocol, scores, and peer replies.
- **Historical** — The monitor pace-health [debate rounds](../history/design/monitor-pace-health-2026-11-23/rounds/) and [scoring rounds](../history/design/monitor-pace-health-2026-11-23/scoring/) preserve the evidence behind the decision record.
- **Historical** — The [design archive](../history/design/) also holds earlier audits and shipped monitor designs. The [archival ledger](../history/ledger-2026-10-17.md) records original commits, hashes, and Git retrieval commands for the design files, reviews, and production call map removed from the tree.
