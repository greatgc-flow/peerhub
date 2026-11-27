# Activity export implementation record

## PH-W0, 2026-11-28 KST

PeerHub v0.28.2 main b1d877d689a9bc79d16054de7371b28b108049fd and design fe12b86c058c3c34aca8ded69e2cf2fe4683e451 differ by the design package alone. GitHub listed zero open PRs, open issues 498, 502, 503 and 504, and successful main CI run 45983685529. Both eight-entry manifests passed, including Engram Rev06 at c59c0e42bb6584bc873269b7128a1c94ecba3229. Reviewed the four maintainer documents, source queries, all twelve requirement groups, PHT001–077, PH-01–14 and RSK01–18, and Engram requirements R05/R06, specification §§7–8, contract §5 and T92–100.

F01 probe on a new synthetic database used the normal ask path with FakeRuntimeTarget. One delivery yielded two records and nine evidence rows, including terminal at sequence 6, response_appended at 8 and offset_acked at 9. An independent mark_outcome write committed while a read transaction was open. The reader saw zero outcomes both before and after the write. Its successor saw (1, accepted). Workspace generation existed. Core schema is 4. Records have unique per-stream positions, while bridge evidence and outcomes have separate append-only integer sequences. The caller row includes private paths and process identity. A raw row export is unsuitable.

F02 baseline on Linux, Python 3.12: pytest on test_ask_metrics.py, test_cc_telemetry_events.py and test_flt_bridge.py yielded 113 passed. This includes #502 fresh-session and ag cache semantics, conservative cc handling, late response/ack and restore crash seams. Missing telemetry is reproducible with a scripted terminal lacking a usage candidate. Historical operator coverage rates were not remeasured. No dispatch, parser or accounting repair is justified by this evidence.

## Contract freeze for first RED

Metadata-only export/show with a per-stream record head and independent bridge evidence/outcome heads, frozen until drained. Return a consumer-owned checkpoint at exhaustion for stores with lineage. Bind the token to schema, store, restore generation and epoch, and exact scope. Cap wildcard partitions and require explicit per-stream enrollment above the cap. Legacy lineage permits only a bounded snapshot. Unknown tables, private payloads, missing receipts and incomplete evidence must remain explicit. Decode only allowlisted receipt fields. Feedback and delivery cannot populate a verified task result. The consumer owns logical source enrollment and conflict quarantine. F08 is a producer contract test; Engram runtime agreement requires a separate consumer run.

## PH-W1 and PH-W2 implementation

The first six CLI tests failed because the activity namespace was absent. They passed after installing the reader and CLI dispatch. Subsequent RED cases identified a separate failed-API usage path, a spoofable delivery ID in arbitrary record metadata, a frozen page that could be stranded by a newly created 33rd stream, linked lineage markers, missing denied-empty status and scope enforcement on single-prompt reads. Each regression now drives the normal source or CLI. Scope is checked in the prompt lookup and in each channel. The first full Linux run found three CLI contract regressions (the explicit leaf inventory, help defaults and exception inventory). The fixes passed the three original gates. Existing Core, bridge/claims, provider execution, CLI defaults and requirements-owner rows have no implementation changes.

The independent 20,000-event fixture drained 10,000 Record and 10,000 evidence rows in 200-item pages with zero repeated or missing event keys. The open writer completed a truncate checkpoint while the last returned page was still retained. The dedicated local test took 1.13 s including its fixture and all assertions. This measures a synthetic workload on this runner. Representative production latency, Windows timing and peak resident memory remain unmeasured.

## Feasibility disposition

| Check | Decision and evidence |
|---|---|
| F01 | G1 supported envelope. Per-stream heads, independent evidence/feedback heads, short WAL readers, restore epoch + generation and physical file identity. Tests cover both crash boundaries and explicit reconciliation across attempts. |
| F02 | Six fake provider families exercised through actual writer and exporter. A failed started API call can persist measured usage in runtime_error instead of session_telemetry. Project that receipt without promoting certainty. Existing quota and parser code stays with its owner. |
| F03–04 | Source review found the stdout callback sets parser_failed on exceptions; AG streaming uses a separate transport. Local path inventory found codex-cli 0.172.0-alpha.2, no claude or agy. Exact observable event grammar, isolated sink and real cross-version parity are NOT_RUN. No G3. |
| F05–06 | Git/file attribution on a shared dirty tree, Windows link containment and protected artifact lifecycle are NOT_RUN. Existing ArtifactStore staging and Eval trace export are available, but their presence establishes neither observation nor permission. No capture writer or redundant journal added. |
| F07 | G5 deferred. A separate PeerHub manual packet has no demonstrated advantage over the planned Engram enrollment/import path. No Web runtime, browser session or archive importer was added. Provider-specific downstream rights have not been assessed on this branch. |
| F08 | Producer publishes version 1.0 and a reproducible three-page exchange. An independent consumer oracle checks retries, digest verification, conflict quarantine and late feedback. Engram-side execution and owner acceptance are pending. |

## Acceptance cross-check against the immutable catalog

The IDs in the middle column abbreviate PHT numbers. A group marked partial below contains at least one limit or unrun case. The listed evidence is scoped to the indicated tests. Original catalog statuses remain the design baseline.

| Requirement | Cases | Candidate evidence and remaining limits |
|---|---|---|
| PHR01 | 001–005 | Core/module graph and M2/M3-disabled exit gates, ordinary ask/replay, logical database dump comparison and offline suite. No new runtime dependencies or ask-path observer. |
| PHR02 | 006–014 | Activity export, bounds and receipts tests exercise explicit DB, filtering, links, pagination, byte budgets, concurrent changes, both recovery boundaries and multi-attempt show. Current Core schema 4 only. Legacy lineage supports one bounded metadata page. Wildcard checkpoints cap at 32 streams. |
| PHR03 | 015–022 | Two real subprocess crash/recovery seams, uncertain followed by authorized retry, accepted→rejected→retried, valid/damaged/wrong-scope tokens, clone and both lineage markers. Consumer oracle quarantines a divergent digest. Unmanaged in-place edits that preserve fences remain outside the contract. |
| PHR04 | 023–031 | Real receipt writes via six scripted runtimes; zero versus absent, first fresh, resumed delta, reset, generation change, failure and replay. Existing ask metrics and cc parser regressions cover cache semantics. Quota, billing, context estimates and Web tokens are outside this export. Actual provider versions/usage remain unmeasured. |
| PHR05 | 032–037 | Negative coverage for private prompt/reply/native identifiers, spoofed metadata, out-of-scope show, linked marker, count/byte boundaries, malformed inputs, future schema and bounded-transaction load. PHT033–034 content consent/redaction is deferred: v1 omits all content. PHT037 peak RSS and production performance remain NOT_RUN. |
| PHR06 | 038–046 | New observer off by construction. P1 grammar, backpressure, fault and replay tests NOT_RUN. Existing fake asks still exercise native parser behavior. |
| PHR07 | 047–052 | DEFERRED. No workspace delta capture or attribution. Windows reparse cases for a future file observer NOT_RUN. |
| PHR08 | 053–058 | DEFERRED. No command executor or report importer. Export identifies user feedback and returns verification UNKNOWN. An accepted response supplies no independently verified test outcome. |
| PHR09 | 059–064 | DEFERRED. No owned trace storage or retention policy; fault, backup and optional observer overhead tests NOT_RUN. |
| PHR10 | 065–070 | DEFERRED. No consumer Web adapter. Optional manual packet tests NOT_RUN. |
| PHR11 | 071–073 | DEFERRED. Bulk history belongs to Engram. Rights checks and cloud coding are separate. |
| PHR12 | 074–077 | Producer fixture and typed consumer mapping delivered. Actual Engram ingestion, multi-source task correlation, real-provider evidence and release gates are open. |

Architecture cross-check: PH-01–04 and PH-13/14 govern the shipped read boundary. PH-05–09 gate the optional observer, Git, command and test evidence; PH-10–12 assign manual cooperation and bulk imports. RSK01/02/04/06/17 have repeatable late-event, lineage, resource, usage and regression checks. RSK03/08/18 are addressed through narrow published fields and independent verification. RSK05/07/09–16 remain relevant if an observer, Web route or Engram promotion is added. Cross-source dedup and permission decisions remain in Engram.

## Engram WP6 handoff

Use [the transport contract](CONTRACT.md) and [the synthetic reference exchange](engram-v1.json) for T92–T99. The fixture SHA-256 is `c09ddb1c314ed95cb976985ac81cf2347676873ebc5d40fad80ca450dfee4c1e`. Run `python -m pytest -q tests/communication/integration/test_activity_contract.py` to regenerate the exchange in memory and compare it field for field with the checked-in vector. The consumer's first probe should transactionally apply first, drain, drain, late, late, require four unique keys and one user outcome, then change a digest and confirm quarantine. The source remains read-only throughout. A real Engram-side run is the next interoperability gate. Private activity and optional P1/P2 routes are unnecessary for this probe.

## Candidate validation

The option-effect suite found a valid base64url cursor beginning with a dash. The CLI interpreted the opaque cursor as a new option. The regression pins that exact envelope and exercises the documented two-argument syntax. Writers now prefix tokens with `a1.`; the reader also accepts the earlier draft encoding. The reference exchange uses the corrected wire bytes.

- `python -m pytest -q tests/communication/integration/test_activity*.py tests/communication/architecture tests/continuity/test_m2_m3_exit_gates.py` exercises the producer, the consumer oracle, import boundaries and extension-disabled execution.
- `python -m pytest -q -n 8`, `python -m pytest -q -m serial_timing`, `python -m pyright`, `python -m tools.command_inventory --check` and `python -m tools.ci_platform verify-marker-inventory --inventory ci/platform_sensitive_tests.txt` are the local candidate gates. [Draft PR 506](https://github.com/greatgc-flow/peerhub/pull/506) carries the candidate-matched results and hosted Actions evidence.
- Six real filesystem/WAL/restore and process-crash cases are registered in the reviewed Windows platform inventory. A Windows skip cannot stand in for an executed test.
- Wheel and sdist builds stay at the existing source version. Installed-wheel smoke runs against a missing synthetic DB outside the checkout and checks exit 5 with no creation.
- The only existing CLI oracle changes add the activity leaf commands and option-effect proofs. No owner requirement text or pinned existing behavior was changed. Live provider validation, Engram-side ingestion, P1, P2 and publication are NOT_RUN.
