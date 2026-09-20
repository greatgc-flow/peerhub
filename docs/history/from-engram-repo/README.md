# AI Collaboration History (Migrated from Engram Repo)

This directory preserves historical AI-collaboration development artifacts migrated from the Engram product repository (`D:\Engram&Peerhub\engram-main-worktree`, GitHub `greatgc-flow/Engram`) on **2026-09-20**.

## Rationale
These files predate and document the multi-peer AI-collaboration system and development process that eventually separated into Engram (the portable dev installer/runtime product) and PeerHub (the AI peer collaboration and governance hub).

Because these files resided under `_sys/docs/history/`, `_sys/data/sessions/`, `_sys/data/proposals/`, and `_sys/data/backlog.json` in the Engram repository, they were needlessly shipped in every end-user Engram release zip archive (~3MB uncompressed). Semantically and historically, they belong to the PeerHub governance and collaboration record, not to Engram's runtime product distribution.

## Structure
- [`protocol-docs/`](protocol-docs/): Historical multi-peer AI-collaboration protocol documents (`PROTOCOL.md`, `PROTOCOL_INVARIANTS.md`, `DEBATE_LOG.md`, `DEBATE_PROTOCOL.md`, `PEER_MANAGEMENT.md`, `peer-rules.md`, `protocol-*.md`, etc.) — earlier iterations of the collaboration standards now housed in `docs-v2/`.
- [`sessions/`](sessions/): Dated development session-archive markdown files (38 files spanning July–September 2026) recording past collaborative sessions, analysis passes, and separation blueprints.
- [`proposals/`](proposals/): Historical architecture proposal artifacts (e.g. `req-analysis-gc-round1.md`).
- [`backlog.json`](backlog.json): Historical 129-item development backlog covering AI-hub operations, routing, telemetry, and governance.

## Provenance & Git History
Full pre-migration git history for all included files is preserved in the Engram repository's commit history (`greatgc-flow/Engram`) up to commit `dca195a`; the removal commit is [`2c19d77`](https://github.com/greatgc-flow/Engram/commit/2c19d77).
