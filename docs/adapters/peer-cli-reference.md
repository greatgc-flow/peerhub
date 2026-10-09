# Peer CLI compatibility reference

This is the current cross-peer operator reference. The authoritative runtime
sources are the [adapter implementation](../../peerhub/extensions/adapters/base.py)
and `peerhub/config_data/model-defaults.toml`; historical snapshots under
`checkpoints/` are provenance only.

## Supported surface

| Peer | Binary / observed version (2026-10-08) | Input / output | Invocation |
|---|---|---|---|
| `ag` | `agy.exe` 1.3.1 | `-p` argument / flat JSON | new |
| `cc` | `claude.cmd` 2.1.293 | stdin / stream JSON | new |
| `cx` | `codex.cmd` 0.161.0 | stdin / JSONL events | `exec`, `exec resume` |

Models and efforts for all providers are defined in the
[model profile manifest](../model-profiles/model-profiles.json).

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

Windows paths with spaces, parentheses, or shell metacharacters can interact
with vendor launchers. Node.js based installations need a usable runtime and
launcher layout; PeerHub's resolver does not install or repair those tools.
Windows console/pipe encodings can also affect non-ASCII rendering. Report the
exact command, environment, `peerhub --version`, and `peerhub diag --json` when
these boundaries cause trouble.
