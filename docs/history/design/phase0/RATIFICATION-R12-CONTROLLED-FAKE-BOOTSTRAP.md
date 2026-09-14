# R12 Ratification Proposal: Controlled-Fake Bootstrap Correction

Document UUID: `915ec99e-65f5-4d20-92a2-ddb04091de4f`  
Date: 2026-07-28  
Status: `PROPOSED`; no source work is authorized until the electorate records
an unanimous final decision against the exact dependency manifest below.  
Electorate: `cc`, `ag`, `cx`  
Canonical-hash policy: SHA-256 of the exact raw UTF-8 bytes at the listed
relative path; no normalization, generated rendering, or historical-file
rewrite is permitted.

## Decision requested

Authorize only provider-free controlled-fake runner TDD under
`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`, subject to these binding corrections:

1. R11's observed crosswalk entry is a transcription defect, not an amended
   historical hash. R12 binds the presently measured raw bytes instead.
2. `PROCESS_DEADLINE` and `PROCESS_TIMEOUT` are the closed runner vocabulary;
   `HARD_DEADLINE` and `HARD_TIMEOUT` are not runner terms. JSON framing and
   parsing precede version/schema negotiation, and mismatch is
   `IDEMPOTENCY_PAYLOAD_MISMATCH`.
3. The 54 behavioral IDs remain the Phase 0 exit set. Before Phase 0 exit, the
   validator must demonstrate red on the real non-captured overlay and green
   only on an isolated all-captured synthetic set; actual exit still requires
   verified `V1_CAPTURE` for every original 54 IDs.
4. AC-01..AC-09 are additive authority-proof specifications, all presently
   `V1_SPEC_ONLY`, explicitly excluded from the runner and behavioral-exit
   sets. Cutover remains unavailable until their captures and separate
   cutover ratification exist.
5. The narrow bootstrap supersession permits red tests/source only to create
   controlled-fake evidence. It excludes the host mutation broker, live Hub
   mutation, provider invocation, production feature work, authority cutover,
   and any Phase 0 exit claim.

## Dependency manifest

| Dependency | Raw-byte SHA-256 |
|---|---|
| `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md` | `30d693621885b5887bbfdff470869e2e3aaab32de71e92a009c6754210d1b422` |
| `SUPERSESSION-AND-CROSSWALK-R1.md` | `2f23dcb7896316ea03c2d3febddba9505d563dd44c09d18fbc067db19821768a` |
| `fixtures/fixture-status-v1.json` | `af9112efb0a36682046f07ba925751d3d223867897ec1351a17f443c0fe3ebf8` |
| `HR-04-06-V1-FIXTURE-SPEC-R1.md` | `fc628ae3a7b54665ce3c4e2a3cde6c631221d3daa8ee50c389e26b25cd67cc2d` |
| `RT-04-06-V1-FIXTURE-SPEC-R1.md` | `ef5eea62b632c14c2a8e2d2ca9ee19eaa67cb4c396c3b9cdcb66906e01747037` |
| `GB-01-03-04-05-V1-FIXTURE-SPEC-R1.md` | `b43054f0eba325c4cd6f70f21937a27d6ae77f7f6bf9a5dea273ef5b9ad33b57` |
| `CJ-02-05-V1-FIXTURE-SPEC-R1.md` | `9dc79f3151e2f4fb66160e51abc5462e963115c3a02125fac20c878790301ad3` |
| `AUTHORITY-PROOF-FIXTURE-COMPANION-R1.md` | `5515af04d0947aea78a5d14198c9f58366d7d721e77d839639797ed468345133` |
| `fixtures/authority-proof-status-v1.json` | `14c7bfef2b9390891bce4add541e51d0eb6e31fcbfd90cc050aefaeb454ca921` |
| `FIXTURE-STATUS-VALIDATOR-CONTRACT-R1.md` | `073b52ac8e36291329edf0e0cbf4a174beb704638fa6e8a88b643ba7e5aa8e43` |
| `TDD-BOOTSTRAP-SUPERSESSION-R1.md` | `2b358a7129e3c101344d66529d62256f9dae67edd7164ccd13967d794cbaa8aa` |
| `RATIFICATION-BLOCKER-RESOLUTION-R1.md` | `5c6865abb99eb369a9cad7ef549f7051acc4ddf9b19c9c0876f44617e1589d7f` |
| `RATIFICATION-R11-CONTROLLED-FAKE-RUNNER.md` | `606b20bba107515d0e84d63df3926123db403a2d49e9da1bb29b777fa8ab7125` |

The non-DP/DT fixture-spec set is positively enumerated in the manifest:
HR-04..06, RT-04..06, GB-01/03/04/05, and CJ-02/05. No dependency is included
by an inferred "everything else" rule.

## Required final-vote record

Each voter must assess the raw bytes and answer `APPROVE` or identify a
concrete blocking defect. A final record must preserve the three substantive
reviews, the exact manifest, date, electorate, and resulting Hub round ID.
Any byte change to a listed dependency invalidates this proposal and requires
a new UUID and round.
