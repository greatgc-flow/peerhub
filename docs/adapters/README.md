# docs/adapters/ — Index

This directory documents the peer CLI adapters supported by PeerHub and the associated CLI command syntax and contracts.

## 1. Active Peer Adapters

| Peer | Adapter Doc | Implementation | Target Peer Binary |
|---|---|---|---|
| **ag** | [g.md](ag.md) | peerhub.adapters.agy_adapter | gy.exe (Antigravity CLI) |
| **cc** | [cc.md](cc.md) | peerhub.adapters.claude_adapter | claude.cmd (Claude Code) |
| **cx** | [cx.md](cx.md) | peerhub.adapters.codex_adapter | codex.cmd (Codex CLI) |

## 2. CLI Reference & Contracts

- [peer-cli-reference.md](peer-cli-reference.md) — Comprehensive reference of supported flags, subcommands, and invocation patterns across all three peers.

## 3. Historical Migration Checkpoints

Location: [checkpoints/](checkpoints/)

Contains verbatim reference snapshots ported from Engram during the 2026-09-03 separation:
- gy.md: Antigravity CLI known bugs & update checkpoint snapshot
- cc.md: Claude Code known bugs & update checkpoint snapshot
- codex.md: Codex CLI known bugs & update checkpoint snapshot

> Note: Files under checkpoints/ contain historical paths and workflow descriptions from the pre-separation repository and are preserved for provenance.
