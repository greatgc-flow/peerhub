# D-CTX Workspace Trust Anchor — Proposal (Voice cc)

**Status:** PROPOSAL ONLY — independent design round to answer Q2/D2, the specific question D0's closure (`peerhub-dctx-d0-closure-2026-09-14.md`) named as blocking D1. No implementation is authorized by this document; it requires independent review and DIR-006 confirmation before any code changes, per D0's own closure terms.
**Date:** 2026-09-14
**Author:** cc (Claude Sonnet 5)
**Answers:** ag.opus's Q2 ("What anchors issuer and workspace trust?") and the renewal's D2 ("Define the issuer and workspace trust anchor").

---

## 1. The question, restated precisely

D2/Q2 asked: given that any process running as the same OS user can `import peerhub` and open `.peerhub/peerhub.sqlite3` directly, what makes one particular workspace DB the authoritative source for a credential, rather than a forged copy an attacker (or, more realistically for v1's actual threat model per D3, a confused/misconfigured process) constructs?

The honest answer for a same-user, same-host, no-cryptography v1 is: **nothing can make a workspace DB resistant to a process that already has the same filesystem access the legitimate DB has.** D3 already settled this — v1 is scoped to "cooperative-local confusion protection," not compromised-worker resistance. So this proposal does not attempt to answer "how do we stop a forger" (unanswerable at this trust tier); it answers the narrower, actually-useful question **D0 left open: how do we stop *accidental* confusion** — a stale credential from a finished attempt, a credential presented against the wrong workspace, a copy-pasted `--actor` flag that doesn't match who was actually dispatched — **using a mechanism whose guarantee is honestly stated, not oversold.**

## 2. The anchor: admission-transaction membership, not a computed secret

**Core idea:** a credential is not a secret that some algorithm can verify out of context. It is a **row that exists in one specific workspace DB, written in the same SQLite transaction as the `dispatch_requests` row it authorizes**, scoped to `(workspace_home_id, activation_epoch)` — both already opaque, already unforgeable-by-construction values from R2's identity system (migration `0032_workspace_activation_epoch.sql`), not caller-suppliable.

Concretely:

1. **New table**, `dispatch_context_credentials` (migration to follow, not part of this proposal — schema sketch only):
   ```sql
   CREATE TABLE dispatch_context_credentials (
       credential_id TEXT PRIMARY KEY,       -- random, unpredictable (secrets.token_urlsafe(32))
       command_id TEXT NOT NULL REFERENCES dispatch_requests(command_id),
       workspace_home_id TEXT NOT NULL,      -- from workspace_identity, not caller-supplied
       activation_epoch INTEGER NOT NULL,    -- from workspace_identity, not caller-supplied
       issued_at INTEGER NOT NULL,
       expires_at INTEGER NOT NULL,
       revoked_at INTEGER,
       CHECK (expires_at > issued_at)
   );
   ```
2. **Issuance happens exactly once**, inside the *same* transaction that admits the command and writes its `dispatch_requests` row (the existing `GovernanceBroker.submit()`/admission path already does CAS-guarded transactional writes — D8's "verify at commit" pattern already exists here to extend, not invent). No separate "credential service" a caller could invoke independently exists; a credential can only come into being as a side effect of a real admission.
3. **Verification is table membership, not computation:** the CLI reads `PEERHUB_CONTEXT_FILE`'s locator (per §8.3's reduced carrier surface), opens the DB it points at, and checks: does `dispatch_context_credentials` contain a non-revoked, non-expired row for this exact `credential_id`, whose `workspace_home_id`/`activation_epoch` match the *current* `workspace_identity` singleton row in *this same DB*? If the DB is a forgery, this check trivially "succeeds" against the forger's own forged row — exactly D2's original complaint — **but that is no longer a gap this proposal claims to close.** A forger who can write `dispatch_context_credentials` could equally write `dispatch_requests`, `mutation_requests`, or run `UPDATE consensus_rounds` directly; D3 already established that a same-user forger already has unrestricted DB access today, with or without D-CTX. D-CTX's actual job is narrower: make an *accidental* mismatch (wrong DB, wrong epoch, stale attempt) fail loudly, not to make a *deliberate* forgery hard.
4. **Epoch binding closes D12 for free.** `activation_epoch` only ever increases (`mint_new_epoch()`/`mint_new_identity_and_epoch()`, R2/R3). A credential minted under epoch N is worthless after a restore bumps the epoch to N+1 — `invalidate_restored_authority()` (R3, `restore_authority.py`) already revokes prior-epoch authority on every restore; extending it to also `UPDATE dispatch_context_credentials SET revoked_at = ? WHERE activation_epoch < ?` in the same pass is a small addition to an existing, tested mechanism, not new design.
5. **Cross-workspace accidental reuse is structurally prevented**, not checked: a credential minted under workspace A's `workspace_home_id` will never match workspace B's `workspace_identity` row, because that ID is opaque and workspace-local (R2 4.3). No comparison logic can be bypassed by a copy-paste error, because there is nothing to copy-paste that would resolve to a different workspace's ID by accident.

## 3. What this explicitly does NOT claim

Matching D3's ship-label discipline exactly:

- **Does not resist a compromised or malicious same-user process.** Such a process can read `dispatch_context_credentials` directly, mint its own row via direct SQL, or copy a live `credential_id` from another peer's file before it's used. None of this is prevented, and this proposal does not claim otherwise.
- **Does not provide cryptographic non-repudiation.** `credential_id` is unpredictable (prevents guessing/brute-forcing across processes that do NOT already have DB access) but is not signed, and possession of it is not tied to any hardware or OS-level identity check.
- **Does provide:** detection of stale, cross-workspace, or already-completed-attempt credential reuse; an audit trail (`issued_at`/`command_id` link back to the real admitted request); and a structural reason a copy-paste or wrong-flag mistake fails closed instead of silently succeeding.

## 4. Relationship to D7 and the carrier

This proposal is independent of D7 (already fixed, `063f24d`) — D7 is a transport-environment bug, this is a credential-storage design. It assumes §8.3's carrier reduction (`PEERHUB_CONTEXT_FILE` only) is still the right target once implementation resumes, and does not reopen that question.

## 5. What remains open even if this is accepted

- **D6** (secure file creation/reader-access measurement) is unaddressed by this proposal — it's about the context *file*, not the DB-side credential table, and needs its own platform-specific measurement work before implementation.
- **D9** (idempotency-hash separation of operation identity from credential-instance evidence) needs the actual mutation-hash code to be touched; this proposal only fixes what goes in `dispatch_context_credentials`, not how `mutation_requests`' own idempotency key is computed.
- **The emergency-human-route question (Q8/D13)** is not answered here and needs its own decision.

This proposal claims to close Q2/D2 specifically, and only that.
