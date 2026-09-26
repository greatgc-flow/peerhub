# Cutover readiness — state after the overnight wiring run (2026-09-27)

Scope: retiring `P:\_sys\core\hub.py` in favor of Engram + peerhub. **The cutover itself has NOT been
executed.** This records what is wired, what is deliberately different, and what needs a human decision.

## Wired from the ratified design (GOVERNANCE-DISPATCH R4/R10)
| Item | Where | Status |
|---|---|---|
| Consensus V2 engine, atomic activation, facade, ACK/NACK/correct/retract CLI | `governance/consensus_shell.py`, `persistence/consensus_activation.py`, `application/consensus_facade.py` | merged; **activated on both real workspace DBs** |
| R4 2.1 ingress: `--query-file`, one-source contract (ask + broadcast) | `application/ingress.py` | merged |
| R4 2.8 declared-hint routing: `--effort-hint`, `--effort-routing`, pin wins | `application/effort_routing.py` | merged (ask + broadcast per target) |
| R4 2.9/B8 headroom: `diag --headroom`, none/basic/full, 24h reliability | `telemetry/reliability.py`, `presenter.render_headroom` | merged |
| R4 2.1/8.1 consultation gate at ask/broadcast ingress | `application/consultation_gate.py` | NONE/NOTIFY proceed; REVIEW/QUORUM/UNANIMOUS **fail closed** (see decisions) |
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

## Open decisions (the ratified text is silent; peers also said "needs a human")
1. **Ask consultation round engine**: who is the electorate/reviewer set for REVIEW/QUORUM on an
   `ask`/`broadcast` (proposal voters? registry? N for `quorum_formula`), what payload the round carries
   (prompt digest? disclosure scope), and whether the ask blocks up to `consensus.timeout_seconds`.
   Until decided a configured REVIEW+ depth blocks dispatch (fail closed); shipped defaults are `none`.
2. **Session rotation (B9)**: how `ask` discovers the session generation/conversation scope, whether to
   expose `--session-policy`, and what a "checkpoint" is per peer CLI. `SessionRotationSaga` still has
   zero callers. (One consulted peer suggested reading legacy `.ai/sessions/...` state — not adopted.)
3. `evidence_circuit_only` health mode: what counts as "typed evidence" at the CLI layer is approximated
   as "the result carries an error code"; confirm.
4. Broadcast legs do not create quarantine reviews yet (per-leg failure semantics unspecified).
5. `NOTIFY` depth: delivery target creation is not implemented (dispatch proceeds, note printed).

## Not verified / TEST NEEDED
* cx.astra confirmation of the batch-E consensus fixes (quota); multiprocess WAL fencing against a
  restarted old binary; real credential/health lifecycle races.

## The cutover itself (not started)
Environment move (`_sys/claude` -> `.engram/claude`, incl. the Claude Code `projects/<slug>` memory folder
rename), retiring `hub.py`, updating peers' invocation from `hub.py ask` to `peerhub ask`, and shadow
validation ("hub.py remains authoritative until shadow validation" — peerhub backlog). Needs a planned
window; it would disrupt the running session.
