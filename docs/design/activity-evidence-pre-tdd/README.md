# PeerHub Activity Evidence and Optional Web Cooperation — Pre-TDD v0.1

Date: 2026-11-28 KST. Status: proposed, not implemented. PeerHub v0.28.2 main b1d877d689a9bc79d16054de7371b28b108049fd; Engram Rev. 0.6 design commit c59c0e42bb6584bc873269b7128a1c94ecba3229. This is the second track of the Engram Cognitive AI Platform. The package is deliberately smaller than a new orchestration or memory subsystem.

**Read first:** [DESIGN.md](DESIGN.md), [CONTRACTS.md](CONTRACTS.md), [TEST_PLAN.md](TEST_PLAN.md), [SOURCES.md](SOURCES.md), [WORK_HANDOFF.md](WORK_HANDOFF.md). [REQUIREMENTS.csv](REQUIREMENTS.csv) and [TEST_CATALOG.csv](TEST_CATALOG.csv) provide machine-readable traceability (12 groups, 77 cases). [MANIFEST.sha256](MANIFEST.sha256) checks the eight other files. All new acceptance cases are NOT_RUN.

| Increment | Decision |
|---|---|
| A / P0 | Generic read-only activity export and show, historical source coverage, token measurements from existing receipts |
| B / P1 | Optional bounded structured CLI observation, scoped file delta and verification evidence, independently enabled |
| C / P2 | Optional manual Web handoff for ChatGPT, Claude, Gemini; user-supplied authorized history import coordinated with Engram |
| D / deferred | Automatic consumer Web operation, universal full transcript, cloud coding adapters, extra storage/daemon; require a demonstrated gap and separate approval |

Core stays Peer/Stream/Record/Offset. Execution truth remains in existing Bridge and claims. No Hindsight dependency, no model calls from export, no default new database or service. The Engram collector owns experience promotion, deduplication across sources, knowledge and skill validation; it can start using existing PeerHub CLI without this extension. The public repository must contain synthetic fixtures only.

This is a design-only commit. No code, live account access, provider calls, deployment, version bump, release or main merge. Current CI success is historical baseline evidence, not acceptance of this proposal. The next stage is feasibility and contract freeze; only then RED tests.
