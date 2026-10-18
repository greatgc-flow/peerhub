# PeerHub Documentation Map

PeerHub is a durable communication layer for collaborating AI peers (`cc`, `cx`, `ag`). The Core owns only Peer, Stream, Record, and Offset. All higher-level coordination primitives are extensions.

## Core & Everyday Guides
- [Profile Smoke Recipe](smoke-recipe.md): Installed-CLI nonce pings and one result matrix (#148).
- [Peer Sandbox Limits](peer-limits.md): Per-peer restrictions, operating knobs, and deferred capability probe (#134).
- [README.md](../README.md): Quick start, installation (`pipx`/`uv`/`pip`), core usage, and everyday operational commands.
- [Common Tasks](../README.md#common-tasks): Worked recipes for default retained-session continuation, blocked stream recovery, offline diagnostics, and package lifecycle.
- [STATUS.md](STATUS.md): Single source of truth for current release status, published version history, and remaining development work.
- [HANDOFF.md](HANDOFF.md): Maintainer operating guidance, coordination rules, and working checkpoints.
- [CONTRIBUTING.md](../CONTRIBUTING.md): Developer environment setup, deterministic testing, release gates, and incident lessons.

## Architecture & Specifications
- [M1 Very Simple Spec](m1_spec/USAGE_GUIDE.md): Architectural boundaries, frozen catalog requirements, and SQLite storage invariants.
- [M2 Extension Contracts](m2/README.md): Specifications for work tracking, skill catalog, artifact store, and runtime adapters.
- [MCP Surface Contract](m2/M2_4_MCP_CONTRACT.md): Integration protocols, JSON-RPC stdio boundary, and session lifecycle.
- [Structure Cleanup](implementation/STRUCTURE_CLEANUP.md): Current component ownership and legacy surface retirement.

## Configuration & Adapters
- [Config Hierarchy](config-hierarchy.md): Workspace, global, and packaged configuration tiers.
- [Model Profiles](model-profiles/model-profiles.json): Provider model defaults, profiles, and refresh schemas.
- [Adapter Guide](adapters/README.md): Vendor CLI prerequisites, authentication, and execution flags (`cc`, `cx`, `ag`).
- [CLI Reference](adapters/peer-cli-reference.md): Low-level adapter options and platform constraints.
- [Operational lessons](lessons.md): Searchable troubleshooting lessons for `peerhub explain <free words>`.
- [Guidance & Session Reference](guidance-reference.md): Status outcome codes, safe recovery actions, and session lifecycle state definitions.
- [Windows Operations](windows.md): Tested PowerShell recipes for parallel asks, quoting, and Unicode paths.

## Active Design & Plans
- [Design Documents Index](design/README.md): Overview of active design specifications, deferred proposals, and history pointers.
- [Pre-TDD Renewal (October 2026)](design/peerhub-pre-tdd-renewal-2026-10-14.md): Reconciled architecture and numbered RED contracts; no implementation authorized by this design stage.
- [CLI, Monitoring, and Inspection Addendum](design/peerhub-cli-monitoring-inspection-2026-10-14.md): MECE command boundaries, profile/session inspection, read-only guarantees, bounded snapshots, human/AI defaults, and PH-T095–105.
- [Monitor Groups Usability Review](design/monitor-groups-usability-review-2026-10-16.md): Unanimous peer decision, group discovery, default/legacy contract, delivery certainty semantics, and TDD acceptance plan.
- [Human-First Monitor Dashboard Redesign](design/monitor-human-dashboard-redesign-2026-10-16.md): Revised unanimous CC/CX/AG decision; visual grouped TTY default, aligned pipe output, JSON/schema compatibility, and group selection.
- [Responsive Monitor Terminal Dashboard](design/monitor-terminal-responsive-2026-10-16.md): Unanimous CC/CX/AG follow-up for height-aware layout, visible DB provenance/safe errors, and cached resize rendering.
- [Monitor Peers View](design/monitor-peers-view-2026-10-16.md): Per-peer quota, profile/model, session reuse, and context group specification (shipped in v0.18.2).
- [Peer CLI Option Audit](design/peer-cli-option-audit-2026-10-15.md): Installed CLI option evidence, legacy cross-check, paid canary results, apply/defer decisions, and required permission/session/streaming canaries.
- [Deepthink Debate Ledger](design/peerhub-deepthink-debate-2026-10-14.md): Debate provenance and corrections; records the three scoped pre-TDD design GOs and remaining implementation/owner/empirical gates.
- [Retained Session Contract](implementation/RETAINED_SESSIONS.md): Registry migration, retention, immutable replay, receipts and current ask output (PR1-3 committed).
- [Canonical Session Sequence](design/analysis-session-reuse-as-is-2026-10-13.md#one-reconciled-pr-sequence): Existing sequence and implementation evidence.
- [Blocked Streams Plan](design/plan-stuck-streams-2026-10-12.md): Existing plan; abandonment and recovery contracts are under review in the renewal.
- [Audit & Triage Record](design/audit-2026-10-12.md): Consolidated October 2026 audit findings, owner decisions, and work packages.

## Archives & Evidence
- [Archival Ledger](history/ledger-2026-10-17.md): Ledger of archived debate documents and obsolete production call-map preserved in git history.
- [Implementation Evidence](implementation/live_evidence/): Live provider canary verification receipts.
- [History Archive](history/): Pre-v0.10 architecture debates, historical design specs, and retired review records.
