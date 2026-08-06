# peerhub
A lightweight, installable coordination layer for orchestrating multiple AI CLI agents (Claude, Codex,   Antigravity, ...) as collaborating peers: dispatch, routing, consensus, and health.

## Status

Implementation in progress (coordination kernel functional).

- **Implemented**: Working coordination kernel covering dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, and SQLite persistence. Verified by 353 passing tests (including 12/12 heartbeat unit tests and 4/4 vertical-dispatch integration tests).
- **Unimplemented Gaps**:
  - Real vendor/CLI adapter integration (only `FakeAdapter` test double is currently implemented).
  - Outbox-to-journal recovery translation.

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/design/peerhub-architecture-debate.md`](docs/design/peerhub-architecture-debate.md).


