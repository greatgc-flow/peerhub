# Peer CLI compatibility reference

This is the current cross-peer operator reference. The authoritative runtime
sources are `peerhub/config_data/model-defaults.toml`, and
`docs/compatibility/peer-cli-contracts.toml`; historical snapshots under
`checkpoints/` are provenance only.

## Supported surface

| Peer | Binary / observed version (2026-10-08) | Input / output | Invocation |
|---|---|---|---|
| `ag` | `agy.exe` 1.3.1 | `-p` argument / flat JSON | new |
| `cc` | `claude.cmd` 2.1.293 | stdin / stream JSON | new |
| `cx` | `codex.cmd` 0.161.0 | argument / JSONL events | `exec` |

| Peer | `standard` | `effort` | `deepthink` | `pro` |
|---|---|---|---|---|
| `ag` | `gemini-3.8-flash-low` | `gemini-3.8-flash-high` | `gemini-3.1-pro-low` | `gemini-3.1-pro-high` |
| `cc` | `claude-haiku-4-5-20251001` | `claude-sonnet-5-5` (`high`) | `claude-opus-5-5` (`high`) | `claude-fable-5-1` (`high`) |
| `cx` | `gpt-6-luna` (`low`) | `gpt-6.1-sol` (`high`) | `gpt-6.1-sol` (`xhigh`) | `gpt-6-astra` (`xhigh`) |

Exact invocation and decoder details are in [ag.md](ag.md), [cc.md](cc.md), and
[cx.md](cx.md).

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

Observed versions and help output are evidence, not self-updating policy. Review
adapter behavior and live results before manually adding a compatible version or
changing a packaged model default.

## External limitations

Vendor authentication, networking, quota telemetry, model retirement, and
provider-side defaults can change independently of PeerHub.
PeerHub preserves these boundaries, emits actionable diagnostics, and does not
silently bypass external security controls.
