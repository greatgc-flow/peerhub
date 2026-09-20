# R4/P4b consensus gateway gap closure

**Status:** implementation specification, 2026-09-20.

## Scope

Close only the four consensus CLI entrances that are still marked as direct
bypasses in `peerhub-production-call-map-R1.json`: `propose`, `proposal-add`,
`proposal-vote`, and `arbiter-review`.  Each will reuse the existing
`_cli_submission()` and `_submit_via_gateway()` helpers.  No task, room, or
lesson files are in scope.

## Command and handler contract

- `ConsensusProposeCommand` gains `verified_required: bool = False`.  Its wire
  parameters include `"verified_required"`.  `decode_propose()` reads that
  parameter, and the registered `consensus.round.propose` handler passes it to
  `ConsensusService.propose()`.
- `ProposalAddCommand` gains `verified_required: bool = False`.  Its wire
  parameters include `"verified_required"`.  `decode_proposal_add()` reads
  that parameter, and the registered `governance.proposal.create` handler
  passes it to `ProposalCoordinator.add_proposal()`.
- `ProposalVoteCommand` gains `credential_id: str | None = None`.  It is not a
  wire parameter: `decode_proposal_vote()` reads `envelope.credential_id`, and
  the registered `governance.proposal.vote` handler passes it to
  `ProposalCoordinator.vote_proposal()`.  The CLI supplies the same value to
  `_submit_via_gateway(..., credential_id=...)`, which is what places it on the
  envelope for gateway verification.
- `ArbiterReviewCommand` and its handler require no contract changes.

No new production methods are introduced. The existing methods changed only at
their argument boundary are `ConsensusService.propose()`,
`ProposalCoordinator.add_proposal()`, and `ProposalCoordinator.vote_proposal()`.
The CLI continues to use existing `_cli_submission()`, `_submit_via_gateway()`,
and `_print_proposal_vote_compatibility()`; the latter changes only from a
`ProposalVoteResult` input to its existing encoded mapping shape.

## CLI behavior and output compatibility

- `consensus propose` submits `ConsensusProposeCommand` with
  `actor_id=parsed.proposer`, request kind `consensus-propose`, the existing
  SHA-256 source hash, and `parsed.verified_required`.  Its existing state-based
  human and JSON output is reconstructed from the target identified by the
  successful gateway result.
- `consensus proposal-add` submits `ProposalAddCommand` with
  `actor_id=parsed.from_peer`, request kind `consensus-proposal-add`, and every
  existing CLI argument including `parsed.verified_required`.  Its encoded
  result already has the exact JSON fields used by the CLI.
- `consensus proposal-vote` preserves the existing voter resolution order:
  explicit `--voter`, resolved credential actor, then the compatibility default
  `cc`.  It submits `ProposalVoteCommand` with request kind
  `consensus-proposal-vote`, passes the credential to both the command and the
  gateway helper, and renders the encoded result without re-invoking the domain
  service.
- `consensus arbiter-review` submits `ArbiterReviewCommand` with no actor and
  request kind `consensus-arbiter-review`; its registered handler returns the
  same mapping shape as the direct coordinator call.
- For every command, a failed `CommandOutcome` prints
  `peerhub consensus: <gateway error>` to stderr and returns `2` before any
  output rendering.  Success remains `0` and preserves the existing text/JSON
  shapes.

## Test cases

1. `test_consensus_commands_wire_contracts` proves the two
   `verified_required` flags are encoded and proposal-vote credentials are not
   encoded as params.
2. `test_cli_consensus_propose_routes_through_gateway_and_preserves_verified_required`
   proves the propose happy path, observes `GovernanceAuthorizer.authorize()`,
   and asserts the persisted policy.
3. `test_cli_proposal_add_routes_through_gateway` proves the proposal-add happy
   path, observes the gateway, and preserves `verified_required`.
4. `test_cli_proposal_vote_routes_through_gateway_with_credential` uses a real
   seeded D-CTX credential, proves its matching voter succeeds, and observes
   the credential on the gateway envelope.
5. `test_cli_proposal_verified_required_rejects_impersonation` remains the
   credential-mismatch regression: the same credential presented for another
   actor exits `2` with the precise verification message. The existing
   missing-credential and invalid-credential-resolution tests remain regression
   coverage for domain policy and CLI actor resolution.
6. `test_cli_consensus_arbiter_review_routes_through_gateway` proves the
   arbiter-review happy path and gateway observation through the disabled/no-fire
   policy mapping, so no external executor can run.
7. Existing compatibility tests retain proposal-vote’s explicit-voter,
   credential-derived-voter, and default-`cc` branches. Together with the
   existing required-credential, impersonation, invalid-credential, and
   missing-actor tests, these cover all branch and error paths unchanged by the
   mechanical routing change.

## Call-map correction

Set all four entries to `gateway: "APPLICATION_API_GATEWAY"`, replace stale
direct-handler text with the registered command/handler route, and add static
`authorizer_behavior`: `ASSERTED_ONLY` for propose, proposal-add, and
arbiter-review; `VERIFIED_WHEN_PRESENTED` for proposal-vote.

## Retrospective documentation correction

The implementation-progress claim that the R4/P4b migration round was complete
is inaccurate while these four call-map entries remain unmigrated.  Correct it
to state that the originally migrated domain batch was complete while the four
consensus CLI gaps remained pending; this is a factual correction only and does
not change the ratified mechanism.
