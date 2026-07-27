# peerhub
A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex,   Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health.

## Status

Pre-implementation. The target architecture has been designed and converged
through a 7-round adversarial review (`ag`/`cx`/`cc`) — 3 rounds of core
architecture debate, a 2-round meta-review (5-Whys/MECE/purpose-fit/
efficiency/feedback-loop), and a 2-round coupling/anti-spaghetti
cross-check that found and fixed a real module dependency cycle — and is
documented in
[`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md). No code exists
yet — implementation starts at Phase 0 of that document's TDD plan, in a
future, separately-authorized round. The full debate record, including
rejected alternatives and evidence citations, is in
[`docs/design/peerhub-architecture-debate.md`](docs/design/peerhub-architecture-debate.md).

