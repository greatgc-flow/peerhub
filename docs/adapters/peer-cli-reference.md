# Peer CLI compatibility reference

This is the current cross-peer operator reference. The authoritative runtime
sources are the [shared CLI runtime](../../peerhub/extensions/adapters/base.py),
the provider implementations for [ag](../../peerhub/extensions/adapters/ag.py),
[cc](../../peerhub/extensions/adapters/cc.py), and [cx](../../peerhub/extensions/adapters/cx.py),
and the [packaged model defaults](../../peerhub/config_data/model-defaults.toml).
See the [adapter checkpoints](../history/adapter-checkpoints/) for historical snapshots.

## Supported surface

| Peer | Binary | Input / output | Invocation |
|---|---|---|---|
| `ag` | `agy.exe` | stdin / stream JSON, or `-p` argument / flat JSON | new, native resume |
| `cc` | `claude.cmd` | stdin / stream JSON | new, native resume |
| `cx` | `codex.cmd` | stdin / JSONL events | `exec`, `exec resume` |

The packaged model defaults record models, efforts, and observed CLI versions.
The [refresh recipe](../model-profiles/model-profiles.json) describes how to update them.

See [ask](../commands.md#ask) for Stream and session selection and [the retained session contract](../implementation/RETAINED_SESSIONS.md) for exact selection and replay rules.

Exact invocation and decoder details are in [ag.md](ag.md), [cc.md](cc.md), and
[cx.md](cx.md).

See the [Peer CLI option audit](../history/design/peer-cli-option-audit-2026-10-15.md)
for locally checked optional flags, legacy findings, and options that need
canaries before changing the production invocation.

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

## Sandbox and event limits

`ask --writable` maps to cc `--permission-mode acceptEdits`, cx `-s workspace-write` and ag `--mode accept-edits`. Codex defaults to `-s read-only`; resume uses `-c sandbox_mode=...` and a mode change requires a compatible new binding. Agy requires explicit `--writable` for native execution (#140), including pings and reviews. Host policy can impose further restrictions. See each adapter above for invocation details and [Windows operations](../windows.md) for host-specific workarounds.

| Peer | Limits | Knobs / next action |
| --- | --- | --- |
| `cc` | Writes only under the `-w` workspace. `gh`/`git` commands and reads outside that workspace require interactive approval, which is never granted non-interactively. | Select the workspace with `-w` / `--workspace`; pass `--writable` for edits (`--permission-mode acceptEdits`). Keep work within that workspace; the caller handles approval-dependent operations. |
| `cx` | The requested sandbox applies alongside host restrictions. | See the [Windows sandbox workflow](cx.md#windows-sandbox-workflow) for observed limits and who runs checks. |
| `ag` | Writes require `--writable`; read-only native `ag` is refused before launch (#140). It prints nothing until done. | Pass `--writable --timeout-seconds 7200 --silence-timeout-seconds 7200`. |

### cc and cx event size

The cx adapter accepts at most 262,144 bytes (256 KiB) per JSONL record,
excluding the newline; this is separate from the total retained output budget
(`--max-output-bytes`). Oversized records are discarded without retaining their
payloads in warnings or telemetry. `oversized_event_count` records the count.
A later intact answer followed by `turn.completed` establishes delivery after a
skipped record; an oversized final answer/completion or an unresolved gap keeps
the outcome uncertain. A completion alone cannot substitute for a lost answer.

The cc adapter uses the same 256 KiB per-line cap on its `stream-json` output. Since 0.24.5 (#325) a
later intact `result` event establishes delivery after a skipped line (typically a large tool result in a
long ask); an oversized `result` event, a gap after the result, or a gap with no result keeps the outcome
uncertain, and session id and usage are not taken from a stream with an unresolved gap.

## Provider Refusal Classification (NOT_EXECUTED)

Provider refusals (quota exhaustion, invalid model selection, authentication failures,
or usage limits) are classified as a distinct `NOT_EXECUTED` task outcome when
post-spawn evidence proves no tool work occurred. The delivery prompt is
acknowledged (`acked=1`) and offset advanced, unblocking the stream without replaying the prompt. Certainty is preserved as `STARTED` per Bridge
monotonicity. Refusals occurring after tool calls remain uncertain (`blocked_uncertain`)
to guard potential workspace side effects.

Classification uses provider error fields, never successful response text. Codex
item start/update/completion events and Claude tool content blocks count as
execution evidence. Missing, malformed, oversized, or unknown event evidence
prevents automatic refusal settlement. Agy emits its flat JSON output only on
completion, so empty stdout cannot prove absence of tool work. Its recognized
nonzero stderr diagnostics with empty stdout and complete retained output qualify
only for historical read-only invocations (`--writable` off). For example, exit 3 with
`Individual quota reached ... Resets in 20h9m47s` is settled as a refusal only
in historical read-only mode. Current native `ag` execution requires `--writable`
and is refused before spawn without it (#140); cached terminal replay still works.
Writable Agy runs remain uncertain (`blocked_uncertain`)
unless output carries explicit pre-execution evidence; Agy provides none today.

Refusal evidence is committed before settlement. Recovery finishes session
invalidation and offset advancement before acknowledging the delivery, without
invoking the provider again. Repeating the same request ID returns its recorded
`NOT_EXECUTED` outcome; a new request uses a fresh session generation.
