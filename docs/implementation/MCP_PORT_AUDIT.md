# MCP and adjacent extension contract audit

This is implementation evidence, not an edit to frozen M2/M3 contracts or a
promotion to VERIFIED. The audited candidate is the current uncommitted tree;
final fixed-SHA CI, live evidence and release gates remain separate.

## Scope and method

Read the complete frozen contracts and implementation for Extension Host,
Artifact, Work, Skill/Catalog, MCP, Eval/Telemetry and A2A. Compared public ports,
authority separation, input handling, lifecycle rules, replay and crash behavior.
Independent regressions test actual storage outcomes instead of only checking
that a contract-labelled function exists. No live provider calls were made.

MCP framing was checked against the pinned 2024-11-05 protocol baseline:
[tools](https://modelcontextprotocol.io/specification/2024-11-05/server/tools),
[prompts](https://modelcontextprotocol.io/specification/2024-11-05/server/prompts).

## Fixed findings

| Slice | Finding and implemented correction |
| --- | --- |
| MCP | Configured Work/Artifact/Skill ports were inert. Optional tools now route through real public extension ports and are advertised only when configured. Added stream/artifact metadata resources. |
| MCP | Resource listing accessed private Core connections. Added public `CoreStore.list_streams` and idempotency lookup; no MCP handler executes SQL or writes files. |
| MCP | Tool results lacked MCP content, prompts used unsupported system role, malformed UTF-8 could escape error handling. Added text content/isError, user prompt role, robust request validation and sanitized internal errors. Compatibility top-level tool data remains alongside protocol content. |
| MCP | Caller-supplied idempotency keys retried with a new timestamp and conflicted. Reuse original committed created_at, retaining Core changed-payload conflict enforcement. Generated keys are returned to the caller. |
| MCP | Host session handles could not enforce author/stream bindings. Trusted host registration accepts immutable author/allowed-stream capabilities; writes validate them and revoked handles fail closed. Registration is not a wire method. |
| MCP | Missing bounded read/upload inputs and partial secret masking. Added bounded slice/upload validation, nested sensitive keys, token text and known credential-path redaction. Binary artifact resources deliberately expose verified metadata only. |
| Host | Regex-only schema checks allowed Core DROP/DELETE, ATTACH and transaction-control bypasses; schema committed before version update. SQLite authorizer now enforces migration write boundaries, transaction-control is host-owned, table-rename results are checked, and schema/version commit together. |
| Host | Entrypoints could escape their source directory; failed imports left ENABLED state; disable retained callbacks. Reject traversal/escape, mark load faults FAILED and evict partial modules, remove disabled callback bindings. |
| Artifact | Short os.write results were ignored. Retry until every chunk is written or fail without success. stage_file now actually stages bytes. |
| Artifact | Forged staged paths, modified staging bytes and corrupted existing blobs could be accepted. Verify stage location/digest/size and existing content; reject link/reparse paths; PermissionError without a verified concurrent winner is a failure, not successful commit. |
| Work | Reducer accepted stale revisions, illegal lifecycle transitions, duplicate creation and cross-home-stream writes. Guard ordered positions, home-stream identity, exact revision increments, state transitions, checkpoint shape and artifact digest. Public creation/checkpoint/link validation added. |
| Skill/Catalog | Activation skipped tamper checks; hidden ancestor directories could exclude the entire source tree from hashing; symlink files were followed. Verify before validation/activation, hash hidden source files except .git metadata, reject source symlinks. |
| Skill/Catalog | Volatile facts nested in maps/lists bypassed quarantine; reducers accepted stale/illegal lifecycle and catalog updates. Recursive quarantine and home-stream/revision/lifecycle guards now reject poisoned events. |
| Eval | NaN, Infinity, boolean/string scores and malformed evaluator results were accepted. Require object results and finite numeric scores; canonical JSON rejects non-finite values. |

Independent tests: `tests/continuity/test_mcp_ports.py`,
`tests/continuity/test_extension_safety_regressions.py`.

## Remaining exit blockers / limits

- Work and Skill/Catalog writers still read a derived projection before append.
  Different authors and mutation kinds can race on the same revision because
  idempotency is author/kind-scoped. Reducer rejection now protects rebuilds,
  but live write results need an authoritative transaction guard and catch-up
  policy. Existing "concurrency" tests previously used sequential calls only.
- Projection rebuild currently clears and writes projection rows across separate
  transactions. A crash or concurrent reader can see partial state; atomic
  publication and boot catch-up require additional integration work. Skill
  rebuild accepts skill_roots but does not rescan them or validate source drift.
- Host lacks a complete boot manifest/dependency reconstruction path and exact
  dependency version matching. Missing entrypoints can still result in metadata
  ENABLED without a runtime module. Python plugin code executes in-process;
  migration authorizers are not a sandbox for arbitrary Python.
- Artifact commits fsync staging content but not the containing directory, and
  public reads load complete blobs into memory. Windows long-path behavior and
  destructive orphan cleanup against concurrent reference creation need explicit
  tests/coordination before broad durability/security claims.
- Skill source tree hashing retains the previous delimiter-based wire digest
  format for compatibility. Versioned length-framed hashing and existing-digest
  migration need a separate design; activation checks do not eliminate filesystem
  TOCTOU or sandbox executable skills. Basic stdlib YAML parsing differs from
  optional PyYAML parsing for inline collections and advanced YAML.
- Eval traces/dataset dataclasses contain mutable nested values; report integrity
  is checked, but run_eval does not yet validate supplied trace/dataset digests
  against their authoritative artifact/source. No automatic feedback mutation is
  implemented or authorized.
- Initial A2A dispatch only fabricated local SUBMITTED references without a
  remote transport. Parent agent owns the explicit transport/fail-closed fix.
  Local state tests do not verify remote A2A protocol interoperability, durable
  restart recovery, cancellation acknowledgement or a real endpoint.
- MCP currently targets trusted local stdio with host-issued capabilities, not
  remote HTTP authentication. The Work model is flat; get_work_tree does not
  invent parent/child edges. Artifact metadata does not claim persisted MIME
  indexing. Full initialize/initialized negotiation and request throttling are
  additional protocol hardening, not proved complete by these regressions.

These limits prevent a blanket M2/M3 Exit PASS claim. Do not overwrite historical
evidence or mark a frozen maturity snapshot VERIFIED from source presence alone.
