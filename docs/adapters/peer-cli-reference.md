# Peer CLI compatibility reference

This is the current cross-peer operator reference. The authoritative runtime
sources are the [adapter implementation](../../peerhub/extensions/adapters/base.py)
and `peerhub/config_data/model-defaults.toml`; historical snapshots under
`checkpoints/` are provenance only.

## Supported surface

| Peer | Binary / observed version (2026-10-13) | Input / output | Invocation |
|---|---|---|---|
| `ag` | `agy.exe` 1.3.3 | `-p` argument / flat JSON | new |
| `cc` | `claude.cmd` 2.1.296 | stdin / stream JSON | new |
| `cx` | `codex.cmd` 0.162.1 | stdin / JSONL events | `exec`, `exec resume` |

Models and efforts for all providers are defined in the
[model profile manifest](../model-profiles/model-profiles.json).

Ordinary `peerhub ask` calls reuse the compatible current peer/Stream session;
otherwise they start fresh with bounded catch-up. Adapter resume capability is
independent of each call, and vendor resume arguments are internal. Use a
dedicated Stream for a separate task.

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

## Provider Refusal Classification (NOT_EXECUTED)

Provider refusals (quota exhaustion, invalid model selection, authentication failures,
or usage limits) are classified as a distinct `NOT_EXECUTED` task outcome when
post-spawn evidence proves no tool work occurred, or a read-only Agy invocation
has no side effects. The delivery prompt is acknowledged (`acked=1`) and offset
advanced, unblocking the stream without replaying the prompt. Certainty is preserved as `STARTED` per Bridge
monotonicity. Refusals occurring after tool calls remain uncertain (`blocked_uncertain`)
to guard potential workspace side effects.

Classification uses provider error fields, never successful response text. Codex
item start/update/completion events and Claude tool content blocks count as
execution evidence. Missing, malformed, oversized, or unknown event evidence
prevents automatic refusal settlement. Agy emits its flat JSON output only on
completion, so empty stdout cannot prove absence of tool work. Its recognized
nonzero stderr diagnostics with empty stdout and complete retained output qualify
only for read-only invocations (`--writable` off). For example, exit 3 with
`Individual quota reached ... Resets in 20h9m47s` is settled as a refusal only
in read-only mode. Writable Agy runs remain uncertain (`blocked_uncertain`)
unless output carries explicit pre-execution evidence; Agy provides none today.

Refusal evidence is committed before settlement. Recovery finishes session
invalidation and offset advancement before acknowledging the delivery, without
invoking the provider again. Repeating the same request ID returns its recorded
`NOT_EXECUTED` outcome; a new request uses a fresh session generation.
