# docs/adapters/ — Index

This directory documents the current peer CLI adapters supported by PeerHub and
their invocation and decoding contracts. Runtime truth is jointly defined by the
adapter source, packaged model defaults, the model profile manifest,
and reviewed observations; no document contains a second independent
configuration surface.

## 1. Active Peer Adapters

| Peer | Adapter Doc | Target Peer Binary |
|---|---|---|
| **ag** | [ag.md](ag.md) | `agy.exe` (Antigravity CLI) |
| **cc** | [cc.md](cc.md) | `claude.cmd` (Claude Code) |
| **cx** | [cx.md](cx.md) | `codex.cmd` (Codex CLI) |
| **ca** | [API peers](../api-credits.md) | `ant` (Claude API CLI) |
| **oa** | [API peers](../api-credits.md) | none: one HTTPS request to the OpenAI Responses API |
| **ga** | [API peers](../api-credits.md) | none: one HTTPS request to the Gemini API |

See [ask](../commands.md#ask) for session selectors and Stream reuse.

## 2. CLI Reference & Contracts

- [peer-cli-reference.md](peer-cli-reference.md) — Comprehensive reference of supported flags, subcommands, and invocation patterns across all three peers.

Historical snapshots are in the [adapter checkpoints](../history/adapter-checkpoints/).
