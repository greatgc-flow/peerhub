# PeerHub Documentation Map

PeerHub is a durable communication layer for collaborating AI peers (`cc`, `cx`, `ag`, `ca`, `oa`, `ga`). The Core owns only Peer, Stream, Record, and Offset. All higher-level coordination primitives are extensions.

## Core & Everyday Guides
- [Account-bound Peers](accounts.md): Immutable cc/cx roots, environment isolation, and quota limitations.
- [Reading the monitor](monitor.md): What every line, column, state word, bar glyph and alert code of `peerhub monitor` means (the target of its `Guide` link).
- [Batch Runs](batch.md): Validate and execute flat parallel asks, interpret statuses, cancel and resume.
- [Remote Ask](remote-ask.md): Running peerhub ask against peers on remote hosts over SSH.
- [Command reference](commands.md) for Core, ask, diagnostics, observation refresh and help.
- [Upgrade procedure](../README.md#upgrade) for `update --check` and `update --plan`.
- [Profile smoke check](../CONTRIBUTING.md#installed-cli-profile-smoke) for installed-CLI nonce pings.
- [README.md](../README.md): Quick start, installation (`pipx`/`uv`/`pip`), core usage, and everyday operational commands.
- [Common Tasks](../README.md#common-tasks): Worked recipes for default retained-session continuation, blocked stream recovery, offline diagnostics, and package lifecycle.
- [STATUS.md](STATUS.md): Single source of truth for the remaining development work and deferred backlog; the release record is CHANGELOG plus the GitHub releases.
- [CONTRIBUTING.md](../CONTRIBUTING.md): Developer environment setup, deterministic testing, release gates, and incident lessons.

## Architecture & Specifications
- The [frozen M1 spec](m1_spec/USAGE_GUIDE.md) records Core and Bridge invariants, the M1 test catalog, and release-gate inputs. The M2 and M3 contracts below cover extension requirements and tests.
- [M2 Extension Contracts](m2/README.md): Specifications for work tracking, skill catalog, artifact store, and runtime adapters.
- [M3 Extension Contracts](m3/): Specifications and test catalogs for federated intelligent collaboration (Search, Memory, A2A, Routing).
- [MCP Surface Contract](m2/M2_4_MCP_CONTRACT.md): Integration protocols, JSON-RPC stdio boundary, and session lifecycle.
- [Database Migrations](migrations.md) for CoreStore schema upgrades and the historical v0 migration workflow.
- [Execution Kernel Boundaries](implementation/EXECUTION_KERNEL_BOUNDARIES.md): Core / logical Kernel / Adapter ownership, concrete refactors and test gates (#335).

## Configuration & Adapters
- [Config Hierarchy](config-hierarchy.md): Workspace, global, and packaged configuration tiers.
- [Model Profiles](model-profiles/README.md) for packaged defaults and the refresh recipe.
- [API peers and credits](api-credits.md) for ca, oa and ga setup, outcomes and billing.
- [Adapter Guide](adapters/README.md): Prerequisites, authentication, and execution options for CLI peers (`cc`, `cx`, `ag`) and API peers (`ca`, `oa`, `ga`).
- [CLI Reference](adapters/peer-cli-reference.md): Low-level adapter options and platform constraints.
- [Operational lessons](lessons.md): Searchable troubleshooting lessons for `peerhub explain <free words>`.
- [Guidance & Session Reference](guidance-reference.md): Status outcome codes, safe recovery actions, and session lifecycle state definitions.
- [Windows Operations](windows.md): Tested PowerShell recipes for parallel asks, quoting, and Unicode paths.

## Active Design & Plans
- [Design Documents Index](design/README.md): Overview of active design specifications, deferred proposals, shipped designs, and history pointers.
- [Retained Session Contract](implementation/RETAINED_SESSIONS.md): Registry migration, retention, immutable replay, receipts and current ask output (current contract).
- [Blocked Streams Plan](history/design/plan-stuck-streams-2026-10-12.md): PRs 4-6 and automated timeout settlement are deferred; see [STATUS](STATUS.md#deferred-backlog-start-triggers).

## Archives & Evidence
- [Work log 2026-11-12/13](history/work-log-2026-11-13.md): 0.24.7-0.24.11 requirements, unanimous decisions, ConPTY and benchmark measurements, releases and lessons.
- [Work log 2026-11-10/11](history/work-log-2026-11-10.md): requirements, decisions, reviews, releases (0.24.5/0.24.6), environment fixes and the quota diet in one place.
- [Archival Ledger](history/ledger-2026-10-17.md): Ledger of archived debate documents, the obsolete production call-map, and (section 1a) the Engram-era and v0 history folders, with `git show` retrieval commands.
- [Benchmarks](benchmarks/): Release-to-release performance and comparative analysis benchmarks.
- [Hard profile benchmark, 2026-11-10](benchmarks/profiles-hard-2026-11-10/README.md): task, grader, reference and first per-profile results; follow-up to the 2026-11-08 ceiling effect, which it only partly resolves (top profiles still tie at 100).
- [Top-tier profile benchmark, 2026-11-10](benchmarks/profiles-top-2026-11-10/README.md): regex-matcher task (115 hidden cases), grader, reference and first results; the cc tiers tie at 115/115 again and `ag.effort` scores below `ag.standard`.
- [Implementation Evidence](implementation/live_evidence/): Live provider canary receipts up to 0.24.12, kept as history. From 0.24.13 the evidence JSON is attached to each GitHub release instead of being committed.
- [History Archive](history/): Work logs, session logs and audit records. The removed v0 design and review folders, and older history, are in Git (see the ledger).
- [M1 implementation records](history/m1_impl/) for waves, delivery checklists and historical evidence.
- [Implementation audits](history/implementation/) for migrations and milestone closure.
- [Design audits and shipped monitor designs](history/design/) for release and design provenance.
- [Compatibility observations](history/compatibility/README.md) and [adapter checkpoints](history/adapter-checkpoints/) for historical store and vendor behavior.
