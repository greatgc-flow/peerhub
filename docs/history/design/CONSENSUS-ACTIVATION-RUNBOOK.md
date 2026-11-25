# Consensus V2 activation runbook (draft, user-gated)

Activation is **never automatic**. Nothing in the runtime calls `activate_consensus_v2`. Do not run
any step below against the real workspace without an explicit go-ahead.

1. **Freeze writers.** Stop every process that opens the workspace DB (CLI runs, dispatch workers,
   old binaries). The epoch bump fences already-open runtimes, but old binaries must not be restarted.
2. **Backup.** Copy the SQLite file *and* its `-wal`/`-shm` after a clean checkpoint; verify the copy
   opens (`python tools/consensus_preflight/consensus_activation_preflight.py <copy>`).
3. **Migrate.** `peerhub workspace init` on the workspace (schema must be at migration 0036).
4. **Preflight.** `python tools/consensus_preflight/consensus_activation_preflight.py <db>` must exit 0:
   no OPEN legacy consensus rounds, no unfinished consensus.* / invariant effects, activation state
   `pre_activation`. Resolve/abandon open rounds and drain their effects until it does.
5. **Rehearse on a copy.** Copy the DB, run `activate_consensus_v2` on the copy, re-run preflight
   (expects `activated`), start a runtime on the copy and drive one high-risk proposal through
   `proposal add/vote` + `consensus ack`. Discard the copy.
6. **Activate** (single transaction; on any error nothing changes): open a connection to the real DB
   and call `peerhub.persistence.consensus_activation.activate_consensus_v2(connection, now=<epoch s>)`.
7. **Verify.** Preflight reports `activated`; a fresh runtime's `state_store.is_consensus_v2_active()` is
   true; create and complete one V2 round.
8. **Rollback** (only before any V2 write): restore the step-2 backup. After V2 writes, rollback needs
   reconciliation of the V2 rounds -- do not simply remove the triggers.

Still TEST NEEDED per cx audit: multiprocess WAL fencing against a restarted old binary, real
credential/health lifecycle races, backup/restore/clone command interplay with the activation epoch.
