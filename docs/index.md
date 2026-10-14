# PeerHub Documentation Map

PeerHub is a durable communication layer for collaborating AI peers (`cc`, `cx`, `ag`). The Core owns only Peer, Stream, Record, and Offset. All higher-level coordination primitives are extensions.

## Core & Everyday Guides
- [README.md](../README.md): Quick start, installation (`pipx`/`uv`/`pip`), core usage, and everyday operational commands.
- [Common Tasks](../README.md#common-tasks): Worked recipes for default retained-session continuation, blocked stream recovery, offline diagnostics, and package lifecycle.
- [STATUS.md](STATUS.md): Current source (v0.16.0), remaining work, and historical evolution log.
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

## Active Design & Plans
- [Pre-TDD Renewal (October 2026)](design/peerhub-pre-tdd-renewal-2026-10-14.md): Reconciled architecture and numbered RED contracts; no implementation authorized by this design stage.
- [CLI, Monitoring, and Inspection Addendum](design/peerhub-cli-monitoring-inspection-2026-10-14.md): MECE command boundaries, profile/session inspection, read-only guarantees, bounded snapshots, human/AI defaults, and PH-T095–105.
- [Deepthink Debate Ledger](design/peerhub-deepthink-debate-2026-10-14.md): Debate provenance and corrections; records the three scoped pre-TDD design GOs and remaining implementation/owner/empirical gates.
- [Retained Session Contract](implementation/RETAINED_SESSIONS.md): Registry migration, retention, immutable replay, receipts and current ask output (PR1-3 committed).
- [Canonical Session Sequence](design/analysis-session-reuse-as-is-2026-10-13.md#one-reconciled-pr-sequence): Existing sequence and implementation evidence; PR1-3 and health fixes are committed (`cfb28aba`). Proposed revisions remain candidates until pre-TDD closure.
- [Monitoring Plan v2](design/plan-monitoring-v2-2026-10-12.md): Existing monitoring proposal; its dependencies and contracts are under review in the renewal.
- [Blocked Streams Plan](design/plan-stuck-streams-2026-10-12.md): Existing plan; abandonment and recovery contracts are under review in the renewal.
- [Audit & Triage Record](design/audit-2026-10-12.md): Consolidated October 2026 audit findings, owner decisions, and work packages.

## Archives & Evidence
- [Implementation Evidence](implementation/live_evidence/): Live provider canary verification receipts.
- [History Archive](history/): Pre-v0.10 architecture debates, historical design specs, and retired review records.
