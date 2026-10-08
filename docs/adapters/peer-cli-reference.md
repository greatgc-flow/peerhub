# Peer CLI compatibility reference

This is the current cross-peer operator reference. The authoritative runtime
sources are the adapter modules, `peerhub/config_data/model-defaults.toml`, and
`docs/compatibility/peer-cli-contracts.toml`; historical snapshots under
`checkpoints/` are provenance only.

## Supported surface

| Peer | Binary / observed version (2026-10-08) | Input / output | Create / resume |
|---|---|---|---|
| `ag` | `agy.exe` 1.3.1 | `-p` argument / flat JSON | new / `--conversation ID` |
| `cc` | `claude.cmd` 2.1.293 | stdin / stream JSON | new / `--resume ID --autocompact auto` |
| `cx` | `codex.cmd` 0.161.0 | argument / JSONL events | `exec` / `exec resume ... ID` |

| Peer | `standard` | `effort` | `deepthink` | `pro` |
|---|---|---|---|---|
| `ag` | `gemini-3.8-flash-low` | `gemini-3.8-flash-high` | `gemini-3.1-pro-low` | `gemini-3.1-pro-high` |
| `cc` | `claude-haiku-4-5-20251001` | `claude-sonnet-5-5` (`high`) | `claude-opus-5-5` (`high`) | `claude-fable-5-1` (`high`) |
| `cx` | `gpt-6-luna` (`low`) | `gpt-6.1-sol` (`high`) | `gpt-6.1-sol` (`xhigh`) | `gpt-6-astra` (`xhigh`) |

Exact invocation and decoder details are in [ag.md](ag.md), [cc.md](cc.md), and
[cx.md](cx.md).

## Verification workflow

Discovery and deterministic contract checks do not spend model tokens:

```powershell
peerhub adapter discover --json
agy.exe --version
claude.cmd --version
codex.cmd --version
python -m tools.peerhub_facts
```

The live gate uses installed vendor CLIs, network access, authenticated accounts,
and may consume quota or incur token cost:

```powershell
python -m tools.peerhub_facts --live
python -m pytest -q -m e2e
```

Observed versions and help output are evidence, not self-updating policy. Review
adapter behavior and live results before manually adding a compatible version or
changing a packaged model default.

## External limitations

Vendor authentication, networking, quota telemetry, model retirement, and
provider-side defaults can change independently of PeerHub. Windows command
wrappers and vendor sandboxes may also reject special-character or aliased paths.
PeerHub preserves these boundaries, emits actionable diagnostics, and does not
silently bypass external security controls.
