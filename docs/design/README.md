# docs/design/ — Design Documents Index

This directory contains architectural specifications, active design proposals, and design reviews for PeerHub.

> **Status Authority:** [`docs/STATUS.md`](../STATUS.md) is the single authoritative source of truth for release readiness, shipped features, and the active development queue. Never treat dated design proposals as current status records.

## 1. Current Active Designs & Implementation Contracts

- [Pre-TDD Renewal (October 2026)](peerhub-pre-tdd-renewal-2026-10-14.md): Reconciled architecture, numbered RED contracts (PH-T001–PH-T094), and multi-peer consensus.
- [CLI, Monitoring, and Inspection Addendum](peerhub-cli-monitoring-inspection-2026-10-14.md): MECE command boundaries, profile/session inspection, read-only guarantees, and PH-T095–105.
- [Monitor Groups Usability Review](monitor-groups-usability-review-2026-10-16.md): Group discovery, default/legacy contracts, delivery certainty semantics, and TDD acceptance.
- [Human-First Monitor Dashboard Redesign](monitor-human-dashboard-redesign-2026-10-16.md): Visual grouped TTY default, aligned pipe output, JSON/schema compatibility, and group selection.
- [Responsive Monitor Terminal Dashboard](monitor-terminal-responsive-2026-10-16.md): Height-aware terminal layout, visible DB provenance, and cached resize rendering (shipped in v0.18.2).
- [Monitor Peers View](monitor-peers-view-2026-10-16.md): Per-peer quota -> profile/model -> latest session -> context view (shipped in v0.18.2).
- [Peer CLI Option Audit](peer-cli-option-audit-2026-10-15.md): Installed CLI option evidence, canary results, and model options.
- [Deepthink Debate Ledger](peerhub-deepthink-debate-2026-10-14.md): Debate provenance, corrections, and pre-TDD gates.
- [Retained Session Contract](../implementation/RETAINED_SESSIONS.md): Registry, retention, replay, and `--session`/`--fresh` selectors (PRs 1-3 shipped in v0.16.0).
- [Remote Ask Guide](../remote-ask.md): Running a peer on a remote server via SSH.

## 2. Deferred Proposals

- [Inbound A2A Server](a2a-inbound-server.md): PROPOSED, NOT IMPLEMENTED (deferred on 2026-10-10).
- [Quota Forecast Plan](plan-quota-forecast-2026-10-12.md): Offline candidate forecasting backtests; runtime promotion deferred pending validation gates.
- [Blocked Streams Plan](plan-stuck-streams-2026-10-12.md): PRs 1-3 shipped in v0.16.0; PRs 4-6 / automated timeout settlement deferred pending contract review.
- [Deferred Monitor Extensions](monitor-peers-view-2026-10-16.md): `monitor-expand-details`, `monitor-e2e-lineage`, `live-evidence-archive`, and full `db-schema-v2` DDL extensions deferred per STATUS.md and HANDOFF.md.

## 3. History & Archival

- [Documentation Archival Ledger (2026-10-17)](../history/ledger-2026-10-17.md): Full record of archived debate docs (`peerhub-session-usage-monitoring-review-*`, `plan-session-reuse-*`, `plan-session-monitoring-*`, `plan-monitoring-v2-*`) and `peerhub-production-call-map-R1.json` with original commits, SHA-256 hashes, retrieval commands, and preserved decisions.
- [Historical Design Archive](../history/design/): Pre-v0.10 designs, Phase 0 specifications, and migrated 2026-07/2026-08 drafts.
- [Historical Architecture Debate](../history/design/2026-07/peerhub-architecture-debate.md): Initial multi-peer architecture convergence.
