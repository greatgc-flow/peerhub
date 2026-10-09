# docs/adapters/ — Index

This directory documents the current peer CLI adapters supported by PeerHub and
their invocation and decoding contracts. Runtime truth is jointly defined by the
adapter source, packaged model defaults, machine-checkable compatibility
contracts, and reviewed observations; no document contains a second independent
configuration surface.

## 1. Active Peer Adapters

| Peer | Adapter Doc | Target Peer Binary |
|---|---|---|
| **ag** | [ag.md](ag.md) | `agy.exe` (Antigravity CLI) |
| **cc** | [cc.md](cc.md) | `claude.cmd` (Claude Code) |
| **cx** | [cx.md](cx.md) | `codex.cmd` (Codex CLI) |

## 2. CLI Reference & Contracts

- [peer-cli-reference.md](peer-cli-reference.md) — Comprehensive reference of supported flags, subcommands, and invocation patterns across all three peers.

## 3. Historical Migration Checkpoints

Location: [checkpoints/](checkpoints/)

Contains verbatim reference snapshots ported from Engram during the 2026-09-03 separation:
- `agy.md` — Antigravity CLI known bugs & update checkpoint snapshot
- `cc.md` — Claude Code known bugs & update checkpoint snapshot
- `codex.md` — Codex CLI known bugs & update checkpoint snapshot

> Files under `checkpoints/` contain historical paths and workflow descriptions
> from the pre-separation repository. They are preserved for provenance and must
> not be used as current operational guidance.
