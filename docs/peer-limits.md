# Peer sandbox limits and knobs

These are observed operating limits; host policy can impose additional restrictions.
PeerHub's `--writable` permits edits but does not remove sandbox limits.

| Peer | Limits | Knobs / next action |
| --- | --- | --- |
| `cc` | Writes only under the `-w` workspace. `gh`/`git` commands and reads outside that workspace require interactive approval, which is never granted non-interactively. | Select the workspace with `-w` / `--workspace`; pass `--writable` for edits (`--permission-mode acceptEdits`). Keep work within that workspace; the caller handles approval-dependent operations. |
| `cx` | Writes work in fresh `--writable` sessions. Sandbox network requests fail (observed proxy `127.0.0.1:9` refused); the `gh` keyring token is invalid inside the sandbox. Git metadata is read-only, so the peer cannot commit/push. Shell commands time out at about 30 seconds; pytest execution is limited. | Use a fresh writable session (`--fresh --writable`); inspect `~/.codex/config.toml`'s `sandbox_mode` setting. Run long jobs in the background and poll; use targeted tests. The caller commits/pushes and runs checks the sandbox cannot complete. |
| `ag` | Writes require `--writable`; read-only native `ag` is refused before launch (#140). It prints nothing until done. | Pass `--writable --timeout-seconds 7200 --silence-timeout-seconds 7200`. |

The `peerhub diag peers` capability probe is **NOT implemented (deferred)**.
Existing diagnostics do not establish sandbox capabilities.

See the [adapter guide](adapters/README.md) and [installed-CLI smoke recipe](smoke-recipe.md).

## cx event size

The cx adapter accepts at most 262,144 bytes (256 KiB) per JSONL record,
excluding the newline; this is separate from the total retained output budget
(`--max-output-bytes`). Oversized records are discarded without retaining their
payloads in warnings or telemetry. `oversized_event_count` records the count.
A later intact answer followed by `turn.completed` establishes delivery after a
skipped record; an oversized final answer/completion or an unresolved gap keeps
the outcome uncertain. A completion alone cannot substitute for a lost answer.
