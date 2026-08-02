# R11 Ratification: Controlled-Fake Runner Bootstrap

Date: 2026-07-28  
Round ID: `r-517f`  
Status: `FINALIZED` / unanimous  
Electorate: `cc`, `ag`, `cx`  
Decision tier: explicit Tier-0 user-authorized formalization of the three
recorded peer final calls.

## Decision

PeerHub may begin TDD **only** for the provider-free controlled-fake runner
defined by the dependency set below. This is a bootstrap authorization for the
first source deliverable, not a general implementation authorization.

The following remain explicitly blocked:

- Phase 0 exit;
- production feature code;
- authority cutover; and
- host mutation broker implementation.

## Formal vote

| Voter | Vote | Evidence |
|---|---|---|
| `ag` | agree | DeepThink final call: R2 hash, scope, terminology, and 19-capture boundary checked. |
| `cx` | agree | DeepThink final call: R2 hash, isolation, evidence requirements, and non-exit boundary checked. |
| `cc` | agree | Fable final call: crosswalk terminology, evidence discipline, isolation, and no scope widening checked. |

The Hub finalized round `r-517f` as unanimous on 2026-07-28. The individual
reviews and the formal vote are both required provenance; a stored vote alone
does not replace the substantive final call.

## Dependency manifest

SHA-256 is calculated over the raw UTF-8 bytes of each dependency file. The
listed values freeze the input set for this authorization; a content change
requires a new ratification record.

| Dependency | SHA-256 |
|---|---|
| `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md` | `30d693621885b5887bbfdff470869e2e3aaab32de71e92a009c6754210d1b422` |
| `DP-DT-CONTROLLED-FAKE-STRATEGY-R1.md` | `edf2350bd1da183368a7a2c79c8e263b62a843e91feefa0ae2d5b874d44c4545` |
| `V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md` | `8be9b316ea51512e776eec036f04fb01feb5461fe4a7e50417aaace059516c9e` |
| `../TDD-READINESS-GATE-R1.md` | `c910bea70b0aa2b2f56f968ffe793c7dd3174fa3e5827bc5cc2d43e661824380` |
| `../TDD-READINESS-INVENTORY-R1.md` | `0c686db7109129e33666c7fd7dc411b787fc6467c7aceecb751b77ecb576c848` |
| `RATIFICATION-BLOCKER-RESOLUTION-R1.md` | `5c6865abb99eb369a9cad7ef549f7051acc4ddf9b19c9c0876f44617e1589d7f` |
| `SUPERSESSION-AND-CROSSWALK-R1.md` | `2f23dc78896316ea03c2d3febddba9505d563dd44c09d18fbc067db19821768a` |

## Enforced bootstrap constraints

1. The test runner uses a fresh isolated root, injected deterministic clock and
   IDs, and deterministic event scripts.
2. It appends canonical journal records before reducing state and never
   auto-replays an uncertain dispatch.
3. It emits canonical transcripts, raw-byte transcript digests, state digests,
   and machine-readable fixture records with an explicit status.
4. It uses `PROCESS_TIMEOUT`, `IDEMPOTENCY_PAYLOAD_MISMATCH`, and
   parse/framing-before-negotiation semantics as stated in the crosswalk.
5. A produced record may be named `V1_CAPTURE` only after that fixture has run
   under this contract. `V1_SPEC_ONLY` remains a non-evidence status.

## Remaining ratification boundary

R11 does not ratify the 19 missing V1 captures, the authority-cutover proof
matrix, the legacy capture records, or any change to Phase 0 gate state. Those
items need their own digest-bound evidence and a new formal unanimous round.
