# Knowledge Placement Policy

This policy governs where operational guidance, safety facts, and recommendations live across PeerHub documentation, CLI commands (`peerhub guide`), and lessons learned.

## 13-Point Policy

1. **Installed-Release Essentials in Guide**: `peerhub guide` is reserved for installed-release essentials: safe execution, uncertainty/retry handling, session continuity, command discovery, and drift reporting/recovery.
2. **Mechanically Backed Efficiency Only**: Include at most 3 efficiency lines in `peerhub guide`, and only where a concrete PeerHub mechanism directly supports the claim.
3. **Reuse and Profiles Scope**: Explain session reuse with compatible peer, store, stream, and binding. Recommend `.standard` as an economical starting choice, not guaranteed cheapest or fastest.
4. **Transport vs Savings**: `--query-file` and `--json` are transport and parsing choices; make no inherent token-saving claim.
5. **Guide Bounds and Token Count Method**: `peerhub guide` must remain within `<=60 lines` and approximately `~900 tokens`. The token count budget is evaluated via whitespace-delimited word count (`len(text.split()) <= 900`) and standard tokenizer heuristics (~1.3 tokens per word), ensuring it fits compactly in model context windows without displacing operational context.
6. **Evidence-Dependent Lessons**: Operational lessons hold evidence-dependent advice accompanied by explicit applicability scope, target version, checked date, and verifiable source commands or logs.
7. **No Shipped Personal Preferences**: Personal language, verbosity, and terminal-balancing preferences (e.g. "English between AIs", prompt/reply language rules) belong in user-controlled configuration or instructions, NOT in shipped defaults or code.
8. **Volatile Facts Excluded**: Dynamic prices, changing provider rate limits, and transient measured timings stay out of `peerhub guide` and static docs.
9. **Rigorous Promotion Review**: Promoting advice from a lesson into `peerhub guide` requires reviewed applicability, verifiable evidence, and immediate first-use value; incident frequency or count serves only as a review trigger, never an automatic inclusion criterion.
10. **Safety Preservation Under Cap Pressure**: Under line or token cap pressure, never drop essential safety guidance. Eliminate duplication, condense prose, or link to docs before removing safety boundaries.
11. **Atomic PR Updates for Safety**: Any code change or fix that alters a safety-relevant fact (peer write boundaries, retry/uncertain handling, store/upgrade hazards, cost side effects, secrets) MUST update guidance and add regression tests in the same PR.
12. **Canonical Policy Location**: This policy is maintained canonically in `docs/knowledge-placement.md` and referenced by `CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE.md`.
13. **Behavioral and Scenario Testing**: Test coverage for guidance must assert guide bounds, parser-valid example commands, safety scenarios, and generated-data parity; avoid brittle keyword-only correctness tests in favor of executable behavior and scenario assertions.
