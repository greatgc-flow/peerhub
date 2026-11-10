# PeerHub Documentation Map

PeerHub is a durable communication layer for collaborating AI peers (`cc`, `cx`, `ag`). The Core owns only Peer, Stream, Record, and Offset. All higher-level coordination primitives are extensions.

## Core & Everyday Guides
- [Account-bound Peers](accounts.md): Immutable cc/cx roots, environment isolation, and quota limitations.
- [Batch Runs](batch.md): Validate and execute flat parallel asks, interpret statuses, cancel and resume.
- [Remote Ask](remote-ask.md): Running peerhub ask against peers on remote hosts over SSH.
- [Knowledge Placement Policy](knowledge-placement.md): Policy governing guidance placement across docs, CLI guide, and lessons.
- `peerhub diag readiness [--json]` reports cached criteria for every effective configured profile, sorted by ID. `READY` means fresh positive verified quota and a matching terminal delivery; it proves neither current provider availability, actual model identity, authorization, nor semantic task success. Shared pools appear once with remaining quota, reset and evidence age. Missing/stale mappings or corrupt evidence stay `UNKNOWN`; absent success, changed binding, failed/uncertain delivery or required native ag writable consent stays unverified. Native ag consent still requires explicit `--writable`. Global `--db` selection applies; a missing store exits 5 without creation. This command never calls providers or writes, and rejects `--probe`. Human overflow is disclosed; JSON schema 1.0 includes every profile without a size guarantee. Issue #145 is closed; deferred scope is recorded in [STATUS](STATUS.md#deferred-backlog-start-triggers).
- `peerhub diag running [--json]` lists lease-active asks: unexpired claim lease matching the delivery's claim generation, unacknowledged, certainty `NOT_STARTED` or `STARTED`. A lease is not proof a process is alive. Rows carry peer, profile/binding, stream, request id, delivery id, certainty, `started_at`/`elapsed_s` (null without valid start evidence) and `lease_expires_in_s`; at most 20 rows with a `truncated` flag. It reads the store only (no provider calls, refresh or writes); a missing store exits 5 without creation.
- [Profile Smoke Recipe](smoke-recipe.md): Installed-CLI nonce pings and one result matrix (#148).
- [Peer Sandbox Limits](peer-limits.md): Per-peer restrictions, operating knobs, and deferred capability probe (#134).
- [README.md](../README.md): Quick start, installation (`pipx`/`uv`/`pip`), core usage, and everyday operational commands.
- [Common Tasks](../README.md#common-tasks): Worked recipes for default retained-session continuation, blocked stream recovery, offline diagnostics, and package lifecycle.
- [STATUS.md](STATUS.md): Single source of truth for current release status, published version history, and remaining development work.
- [HANDOFF.md](HANDOFF.md): Maintainer operating guidance, coordination rules, and working checkpoints.
- [CONTRIBUTING.md](../CONTRIBUTING.md): Developer environment setup, deterministic testing, release gates, and incident lessons.

## Architecture & Specifications
- [M1 Very Simple Spec](m1_spec/USAGE_GUIDE.md): Architectural boundaries, frozen catalog requirements, and SQLite storage invariants.
- [M1 Implementation Records](m1_impl/): Wave execution reports, verification matrices, and delivery checklists for milestone 1.
- [M2 Extension Contracts](m2/README.md): Specifications for work tracking, skill catalog, artifact store, and runtime adapters.
- [M3 Extension Contracts](m3/): Specifications and test catalogs for federated intelligent collaboration (Search, Memory, A2A, Routing).
- [MCP Surface Contract](m2/M2_4_MCP_CONTRACT.md): Integration protocols, JSON-RPC stdio boundary, and session lifecycle.
- [Database Migrations](migrations.md): CoreStore schema versioning, upgrade policies, and historical v0 migration workflow.
- [Structure Cleanup](implementation/STRUCTURE_CLEANUP.md): Current component ownership and legacy surface retirement.

## Configuration & Adapters
- [Config Hierarchy](config-hierarchy.md): Workspace, global, and packaged configuration tiers.
- [Model Profiles](../peerhub/config_data/model-defaults.toml): Profiles SSOT since v0.23.0. [model-profiles.json](model-profiles/model-profiles.json) is the refresh recipe.
- [Adapter Guide](adapters/README.md): Vendor CLI prerequisites, authentication, and execution flags (`cc`, `cx`, `ag`).
- [CLI Reference](adapters/peer-cli-reference.md): Low-level adapter options and platform constraints.
- [Compatibility Archive](compatibility/): Historical observations and external peer CLI binary compatibility records.
- [Operational lessons](lessons.md): Searchable troubleshooting lessons for `peerhub explain <free words>`.
- [Guidance & Session Reference](guidance-reference.md): Status outcome codes, safe recovery actions, and session lifecycle state definitions.
- [Windows Operations](windows.md): Tested PowerShell recipes for parallel asks, quoting, and Unicode paths.

## Active Design & Plans
- [Design Documents Index](design/README.md): Overview of active design specifications, deferred proposals, shipped designs, and history pointers.
- [Latest plan: 0.24.5 (November 2026)](design/plan-0.24.5-2026-11-10.md): Profile cards, quota utilization report and model re-observation; its decision log also covers the 0.24.6 follow-up (`diag running`, monitor accounts, #327/#328). Previous: [0.24.4](design/plan-0.24.4-2026-11-09.md).
- [Retained Session Contract](implementation/RETAINED_SESSIONS.md): Registry migration, retention, immutable replay, receipts and current ask output (current contract).
- [Blocked Streams Plan](design/plan-stuck-streams-2026-10-12.md): PRs 4-6 and automated timeout settlement are deferred; see [STATUS](STATUS.md#deferred-backlog-start-triggers).

**Shipped designs:** the 0.24.0/0.24.1 plans, the October 2026 renewal, monitor and session designs, and the October audit are kept in `docs/design/` for provenance. They are listed in [Design Documents Index](design/README.md) and are not current status; [STATUS](STATUS.md) is.

## Archives & Evidence
- [Work log 2026-11-10/11](history/work-log-2026-11-10.md): requirements, decisions, reviews, releases (0.24.5/0.24.6), environment fixes and the quota diet in one place.
- [Archival Ledger](history/ledger-2026-10-17.md): Ledger of archived debate documents, the obsolete production call-map, and (section 1a) the Engram-era and v0 history folders, with `git show` retrieval commands.
- [Benchmarks](benchmarks/): Release-to-release performance and comparative analysis benchmarks.
- [Hard profile benchmark, 2026-11-10](benchmarks/profiles-hard-2026-11-10/README.md): task, grader, reference and first per-profile results; follow-up to the 2026-11-08 ceiling effect, which it only partly resolves (top profiles still tie at 100).
- [Top-tier profile benchmark, 2026-11-10](benchmarks/profiles-top-2026-11-10/README.md): regex-matcher task (115 hidden cases), grader, reference and first results; the cc tiers tie at 115/115 again and `ag.effort` scores below `ag.standard`.
- [Implementation Evidence](implementation/live_evidence/): Live provider canary verification receipts.
- [History Archive](history/): September 2026 design records, session logs, and retired review records. Older history is in Git (see the ledger).
