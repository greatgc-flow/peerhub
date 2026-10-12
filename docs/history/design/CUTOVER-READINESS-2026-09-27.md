# Cutover completion — Engram + peerhub (2026-09-27)

Scope: retiring `P:\_sys\core\hub.py` in favor of Engram + peerhub. **The filesystem and runtime cutover
was completed on 2026-09-27.** This records what is wired, what is deliberately different, what was
retired, and which product follow-ups remain outside the cutover.

## Wired from the ratified design (GOVERNANCE-DISPATCH R4/R10)
| Item | Where | Status |
|---|---|---|
| Consensus V2 engine, atomic activation, facade, ACK/NACK/correct/retract CLI | `governance/consensus_shell.py`, `persistence/consensus_activation.py`, `application/consensus_facade.py` | merged; **activated on both real workspace DBs** |
| R4 2.1 ingress: `--query-file`, one-source contract (ask + broadcast) | `application/ingress.py` | merged |
| R4 2.8 declared-hint routing: `--effort-hint`, `--effort-routing`, pin wins | `application/effort_routing.py` | merged (ask + broadcast per target) |
| R4 2.9/B8 headroom: `diag --headroom`, none/basic/full, 24h reliability | `telemetry/reliability.py`, `presenter.render_headroom` | merged |
| R4 2.1/8.1 consultation at ask/broadcast ingress | `consultation_gate.py`, `consultation_round.py` | NONE/NOTIFY proceed; REVIEW/QUORUM/UNANIMOUS run a V2 round (fail closed pre-activation) |
| R4 7.1 health consequence after a definitive ask failure | `application/health_consequence.py` | merged (ask; broadcast legs not yet) |
| R4 6.2 scratch janitor: ownership + liveness, never age alone | `adapters/prompt_transport.py` | merged |
| Final Call reachable from the CLI (`consensus ack/nack/correct/retract`) | see above | merged |

## Deliberately different from hub.py (ratified, not gaps)
* `collab_rate` (0-10) is dropped; replaced by the 5-value `ConsultationDepth` from `dispatch-policy.toml`
  (R2 cross-review "Numeric collab_rate: Dropped entirely"; R10 FINAL ratification).
* No prompt-complexity auto-routing: only a declared `--effort-hint` + `routing.preference_map`,
  default `advisory` (off unless `opt-in`).
* Blind auto-quarantine on any error report is permanently rejected; quarantine requires an authorized
  review/ESCALATE (Gap 3/4).

## Decisions taken 2026-09-27 (cx.astra + ag.deepthink consulted; adopted where they agree, otherwise the
## option that is simpler AND consistent with R4: fail-closed, no invented evidence)
| # | Decision | Implemented in |
|---|---|---|
| D1 | Ask/broadcast REVIEW/QUORUM/UNANIMOUS run a consultation round on the V2 engine: electorate = proposal voters, health-gated, de-duplicated; REVIEW = quorum 1 with the first eligible non-proposer (none -> held); the round stores the query DIGEST only, the exact text is an ephemeral staged copy removed afterwards; ask polls every 250 ms up to `consensus.timeout_seconds`; peers vote with `peerhub consensus vote` (now accepts `block`); approved -> dispatch, dissent -> held, timeout -> REVIEW proceeds / QUORUM+UNANIMOUS escalate and hold; fails closed before V2 activation | `application/consultation_round.py`, `consultation_gate.py` |
| D2 | First increment only: `ask --session-policy reuse\|auto\|fresh`, `--session-id`, `--room-id`; fresh starts the NEXT generation; auto follows `session.default_mode`. Pressure-based rotation (SessionRotationSaga) NOT wired: needs telemetry pressure evidence and a per-peer checkpoint mechanism (no uniform native command; the legacy `.ai/state.json` suggestion was rejected) | `cli/commands/daily.py`, `direct_ask.py` |
| D3 | `DirectAskResult` now carries the attempt's real error code and execution certainty (it was always None, so the R4 7.1 hook never fired); circuits open only for definitive failures (HC-06) | `direct_ask.py` |
| D4 | Each definitive failed broadcast leg creates its own review like ask; unstarted/uncertain/completed legs create nothing | `broadcast.py`, `health_consequence.py` |
| D5 | NOTIFY records a durable `dispatch-notification` target (digest only, recipients, `undelivered`); never blocks | `application/notification.py` |

## Still open
* Pressure-based session rotation + checkpoints (B9) -- deliberately deferred, see D2.
* NOTIFY delivery (mailbox/read receipts) -- state stays `undelivered`.
* Consultation "children" (delivering the exact query to reviewers automatically); today reviewers read
  the ephemeral copy path printed in the round and vote via the CLI.

## Not verified / TEST NEEDED
* cx.astra confirmation of the batch-E consensus fixes (quota); multiprocess WAL fencing against a
  restarted old binary; real credential/health lifecycle races.

## Cutover execution (completed 2026-09-27)
* The active environment is the Engram-managed portable root. Claude, Codex, Antigravity, and peerhub
  resolve their configuration beneath `.engram`; peerhub discovers all three active adapters there.
* peerhub was force-reinstalled from the current `main` checkout and its installed runtime files were
  hash-compared with that checkout. Engram `doctor` reports the active environment healthy.
* The legacy `P:` registration was cleanly unregistered, including its host integrations. The legacy
  combined tree and the superseded standalone Engram tree were removed after their Git histories and
  private state had been preserved.
* Every local-only Git tip from the legacy trees was pushed to a dated `archive/` branch. Complete Git
  bundles were also verified before deletion, so retirement did not discard repository history.
* Validation at the cutover revision completed with 584 passed / 3 skipped / 6 xfailed for Engram and
  2,372 passed / 6 skipped / 15 deselected / 13 subtests passed for peerhub; pyright reported zero
  errors and warnings.

The items under **Still open** are post-cutover product increments. They do not require retaining
`hub.py`, the `P:` mapping, or either retired filesystem tree.
