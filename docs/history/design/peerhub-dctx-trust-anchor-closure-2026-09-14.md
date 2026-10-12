# Q2/D2 closure — trust-anchor proposal accepted

**Status:** CLOSED. DIR-006 unanimous agreement reached 2026-09-14 (cc proposer, ag.opus ACCEPT, cx.standard AGREE).
**Scope:** Closes the specific D0 follow-up item named in `peerhub-dctx-d0-closure-2026-09-14.md` point 4 — a reviewed, concrete answer to Q2/D2. Authorizes beginning D1's implementation for the credential/epoch layer specifically; does not authorize the full carrier/CLI/adapter integration in one step (see "What remains" below).

## Voice record

- **cc** (`peerhub-dctx-trust-anchor-proposal-cc-2026-09-14.md`): proposer.
- **ag.opus** (design-grade independent review, re-verified `mint_new_epoch`/`mint_new_identity_and_epoch`/`_check_generation` directly against source): **ACCEPT**, with one non-blocking clarification (verification must match `command_id`, not just `credential_id` + workspace identity — incorporated in `1c37ae7`).
- **cx.standard**: **AGREE**. "Authorize the next design step; implementation still requires its own review."

## What this authorizes

The `dispatch_context_credentials` table and its issuance (inside the same transaction as command admission) / verification (table membership scoped to `workspace_home_id`/`activation_epoch`/`command_id`) primitives, as a bounded, independently-reviewable increment — analogous to how R2's identity/epoch system and R3's restore-authority system were each built and reviewed as their own increment rather than as part of one large D-CTX drop.

## What remains held

- **The carrier itself** (`PEERHUB_CONTEXT_FILE` env var, per §8.3): needs D5 (transient-namespace placement) and D6 (measured, platform-tested file permissions/reader-access) resolved with real Windows/POSIX canaries before any file-based delivery mechanism ships. Not attempted in this increment.
- **CLI/adapter wiring**: actually consuming a presented credential in `peerhub ask`'s admission path, and any adapter-side environment variable emission, is separate work that touches the live dispatch path and needs its own review once the underlying primitive exists and is tested.
- **D9** (idempotency-hash separation) and **D13's emergency-human-route** question remain fully open, as the trust-anchor proposal itself said.
