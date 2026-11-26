# PeerHub documentation

## Use PeerHub

- [Quick start](../README.md) covers installation, provider setup, the first ask, and the Core recipe.
- [Command reference](commands.md) lists the CLI commands and links to their procedures.
- [Monitor guide](monitor.md) explains the dashboard, quota fields, refresh cadence, and alerts.
- [Batch runs](batch.md) covers manifest validation, profile selection, concurrency, and recovery.
- [Remote asks](remote-ask.md) covers dispatch over SSH.
- [Windows operations](windows.md) covers quoting, Unicode paths, and PowerShell job lists.
- [Recovery and session guidance](guidance-reference.md) defines outcomes and safe recovery steps.
- [Operational lessons](lessons.md) lists searchable symptoms and fixes for `peerhub explain`.
- [Upgrade procedure](../README.md#upgrade) covers planning, backups, and installation checks.

## Configure PeerHub

- [Configuration hierarchy](config-hierarchy.md) covers store selection and workspace, global, and packaged settings.
- [Accounts](accounts.md) explains isolated Claude Code and Codex account bindings.
- [Model profiles](model-profiles/README.md) records packaged model choices and the catalog refresh procedure.
- [Adapter reference](adapters/peer-cli-reference.md) covers execution and sandbox behavior.
- [API peers](api-credits.md) covers credentials, billing, and provider-specific outcomes.

## Develop and maintain

- [Contributing](../CONTRIBUTING.md) covers development setup, testing, release gates, and issue reporting.
- [Project status](STATUS.md) tracks current work and deferred items.
- [Changelog](../CHANGELOG.md) records changes by release.
- [Database migrations](migrations.md) covers CoreStore schema upgrades.
- The [frozen M1 spec](m1_spec/USAGE_GUIDE.md) records Core and Bridge invariants, the M1 test catalog, and release-gate inputs.
- [M2 extension contracts](m2/README.md) cover work tracking, skills, artifacts, adapters, and MCP.
- [M3 extension contracts](m3/) cover search, memory, A2A, and routing.

## Background

- [Design records](design/README.md) collect proposals and design decisions.
- The [Blocked Streams Plan](history/design/plan-stuck-streams-2026-10-12.md) records deferred PRs 4–6 and automated timeout settlement. See [deferred backlog triggers](STATUS.md#deferred-backlog-start-triggers).
- [Execution kernel boundaries](implementation/EXECUTION_KERNEL_BOUNDARIES.md) records the separation of Bridge, the runtime port, and adapters.
- [Retained session implementation](implementation/RETAINED_SESSIONS.md) records registry migration, replay, and receipts.
- [Observation probe implementation](implementation/OBSERVATION_PROBES.md) records provider probe behavior and usage costs.
- [History](history/) holds work logs, audits, and the [archival ledger](history/ledger-2026-10-17.md).
- [2026-11-26 work log](history/work-log-2026-11-26.md) records the requirements audit and follow-ups.
- [Lesson observations](history/observations.md) record the dated measurements behind operational guidance.
- [Archived M1 skill examples](history/m1_spec/05_SKILLS/) preserve the original skill catalog examples.
- [Design audits and shipped monitor designs](history/design/) record release and design provenance.
- [Benchmarks](benchmarks/) hold performance and model comparisons.
- [Archived release evidence](implementation/live_evidence/) holds canary receipts through 0.24.12. Later receipts accompany the [GitHub releases](https://github.com/greatgc-flow/peerhub/releases).
