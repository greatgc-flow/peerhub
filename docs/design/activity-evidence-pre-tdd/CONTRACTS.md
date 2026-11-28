# Proposed contracts v1.0 — review before RED

All CLI and data structures below are new proposals except existing commands explicitly identified. No claim of implemented interfaces. Align with Engram Rev06 CONTRACTS §5. JSON UTF-8, timezone-aware UTC or null with reason, stable IDs, strict finite nonnegative counts, canonical sorted-key serialization fixed by RED fixture, full SHA-256 of allowed canonical payload. Unknown is null plus coverage/reason; zero only measured. Do not reuse credential or vendor private identifier as public ID. Keep binary content separate and never use exported path as executable input. Validate version, scope, size and identity before effects. Existing numeric exit codes unchanged; assign new symbolic errors only after reviewing actual registry and parser. JSON mode returns one bounded object; stderr redacted; no partial valid-looking object on failure.

## A. Proposed read CLI (P0)

```powershell
peerhub --db <approved-existing-db> activity export --json
peerhub --db <approved-existing-db> activity export --stream <id> --cursor <token> --limit 100 --max-bytes 1048576 --json
peerhub --db <approved-existing-db> activity show <ask-record-id> --json
peerhub --db <approved-existing-db> activity show <ask-record-id> --include-content --json
```

Default metadata-only. Content flag explicit, independent consumer consent; per source scope policy also enforced. No implicit DB creation, provider calls, migration, recovery, network or offset write. `--stream` exact filter, optional repeatable peer/time filters deferred (scope must bind all supported filters); `show` supports continuation for all related attempts, not just latest; choose syntax during freeze; same output envelope. Cap both event count and bytes, validate positive range and reserve overhead; output omission event with original reference/digest only when authorized and reason TOO_LARGE/REDACTED/NOT_ENROLLED, cursor proceeds after explicit event; if even omission cannot fit, fail without advancing. Export no raw SQL row, result_json, environment, full CLI argv, private native session ID, auth or secret path. Existing CLI no new meaning for `record read`, `session show`, `profile stats`, `outcome`.

Example synthetic response:
```json
{
  "schema_version": "1.0",
  "source": "peerhub",
  "store_ref": "store-fixture",
  "lineage": "generation-fixture",
  "coverage": {"state": "PARTIAL", "reason": "native_trace_not_enrolled"},
  "channels": [
    {"kind":"record","state":"MEASURED","watermark":"stream-A:2"},
    {"kind":"delivery_evidence","state":"MEASURED","watermark":"8"},
    {"kind":"outcome","state":"MEASURED","watermark":"3"}
  ],
  "items": [
    {
      "source_event_id":"record:record-fixture-1",
      "source_revision":"1",
      "kind":"ai.prompt",
      "record_id":"record-fixture-1",
      "stream_id":"stream-A",
      "position":1,
      "request_id":"request-fixture",
      "actor":"lead",
      "peer_id":"cc",
      "occurred_at":"2026-11-28T00:00:00Z",
      "coverage":{"state":"MEASURED","reason":null},
      "content":{"mode":"OMITTED","reason":"METADATA_ONLY"},
      "source_ref":{"kind":"record","id":"record-fixture-1"}
    }
  ],
  "next_cursor":"opaque-fixture",
  "has_more":true
}
```

Engram envelope mapping: source_instance_id assigned by enrolled store identity; source_event_id/revision preserved, source_lineage separate from event identity; task_id/project_id inferred only from trusted caller or exact mapping else null. Distinct provider/session/attempt/task; refs include store, stream, record, delivery, invocation, native correlation where allowed. Ingested_at is consumer time, occurred_at actual source time or unknown. source_digest of permitted payload distinct from raw source digest; content omissions explicitly typed. A provider response is source evidence, not verified fact. A raw immutable event can support more than one derived memory; not duplicate success. Cross-source links by exact IDs or user mapping; heuristic only AMBIGUOUS_LINK.

### B. Cursor and snapshots

Use existing workspace generation/restore.epoch/store identity evidence as available; verify session cursor implementation as reference, do not initialize missing identity. Legacy unknown store identity: bounded snapshot only, no claims stable incremental cursor. No reliance on DB path alone (move/clone), no assumption global sequence. Channels:
- Record: per-stream immutable position, enumerate stable stream IDs in bounded pages; capture per-stream head under read snapshot for batch, re-enumerate from beginning each poll (new IDs may sort before previous). No monotone cross-stream cursor.
- Bridge evidence: append-only seq, include allowlisted kind and evidence revision; bridge_deliveries.seq is not enough. For delivery created before terminal or response, return raw certainty and ack flag as observed at snapshot with observation timestamp; evidence revision and record append not conflated. Response append, offset ack, refusal, incomplete, reconciliation each own event, exact joining from persisted IDs. Preserve multiple attempts. Current mutable summary is read-time observation, not as-of historical event unless evidence proves it.
- Outcome: bridge_ask_outcomes seq independent, feedback later accepted/rejected/retried; preserve all revisions; no semantic correctness inference. Session events/telemetry may use distinct existing channels or versioned bounded snapshots; preserve actual usage scope.
- Optional trace and manual receipts: independent source-owned committed manifest/sequence, not delivery seq; explicit unavailable until enrolled, missing/restore partial.

Read snapshot for each page (bounded), bind cursor to version, store identity, lineage, scope/filter, content mode, channel, current high-water and position. Do not pin transaction across consumer processing. For stable immutable channels replay page yields same event IDs; read-time mutable summary may change, with separate observation identity/digest and no false claim exact replay. On end page emit resumable checkpoint (if incremental mode supported), no-more for current watermark, next poll new watermark; freeze representation of both continuation and checkpoint (Engram next_cursor null at exhausted snapshot acceptable only if distinct durable checkpoint exists). Never emit one cursor that skips late outcome/evidence. Avoid unbounded inventory tokens: per-channel/per-stream consumer checkpoint or bounded continuation; prove algorithm on concurrent streams, cap state. If no-gap behavior cannot be demonstrated without invasive change, explicit PARTIAL and bounded overlap/resnapshot, not fake completeness. Consumer persists received events and checkpoint transactionally and deduplicates; export never writes cursor or PeerHub offsets. Unknown/mismatched/tampered cursor, unsupported schema, restore/clone or changed consent => invalid/resnapshot; no automatic broaden scope. Cursor is not authentication; validate type/length, do not deserialize arbitrary objects or trust path/SQL from it. Scope authorization by calling policy. Duplicate stable key/different digest quarantine conflict in consumer. Identical immutable evidence across restore dedup; vanished old events remain historical and require current revalidation. Source revocation means stop processing, notify consumer to invalidate derived memory, no core delete.

### C. Event taxonomy

| Kind | IDs and permitted payload | Authority / caveat |
|---|---|---|
| ai.prompt / ai.response | record_id, reply_to, targets, position, binding safe fields, response/delivery/execution refs, optional redacted text | Core record; original vs injected context separate |
| peer.delivery / peer.evidence | delivery_id, attempt, record_id, evidence seq/kind, certainty, acked, response_record_id, bounded reason/status | execution ledger; no success inference |
| peer.outcome | record_id, outcome seq, mark timestamp, accepted/rejected/retried | owner feedback, not task proof |
| peer.session | public handle, generation, fresh/reuse, capability, receipt ref | scoped snapshot; no vendor credentials |
| peer.usage | receipt ref, scope, reported/normalized/attributable fields, coverage | measured vs unknown; no repeated cumulative totals |
| tool.call / tool.result | execution/turn/call/item IDs, actor, category, approved argument/result refs, status | optional provider-reported, not hidden reasoning |
| file.delta | before/after snapshot refs, relative locator, hash/mode/status, attribution confidence | observed delta, not necessarily peer effect |
| command.run | call ref, execution status, exit/duration where reported, safe command/artifact refs | no arbitrary shell execution |
| test.report | owner-approved run/commit/artifact ID, counts, exit, runner/source, conflict | actual evidence or reported, not automatic pass |
| web.packet / web.receipt | local manual packet/receipt ID, hash, claimed provider, scope, timestamps, content ref | user attested, not bridge delivery |
| coverage.gap | channel, reason, bounds, missed count if known | gap explicit, never invent unseen count |

Delivery states retain existing certainty NOT_STARTED/MAY_HAVE_STARTED/STARTED/TERMINAL, ack and result dimensions independently; preserve original public status/refusal reason, output truncation and partial_response where permitted. Completion of delivery is not proof of useful answer. Never replay unresolved ask to fill evidence. Unknown schema or kind must not become successful effect; version negotiation and unsupported coverage.

### D. Usage (example)

```json
{
 "kind":"peer.usage","receipt_ref":"receipt-fixture-1","delivery_id":"d-fixture",
 "scope":"session_cumulative","attribution":"unattributed",
 "source":"cc.result.usage","coverage":{"state":"MEASURED","reason":null},
 "reported":{"input_tokens":100,"cache_read_input_tokens":400,"cache_creation_input_tokens":20,"output_tokens":30},
 "normalized":{"uncached_input_tokens":100,"cache_read_input_tokens":400,"cache_creation_input_tokens":20,"provider_input_tokens":520,"output_tokens":30},
 "delivery_measurement":{"input_tokens":null,"output_tokens":null,"reason":"no_comparable_previous_receipt"},
 "field_evidence":{"provider_input_tokens":"derived_from_reported_components"}
}
```

Synthetic numbers; existing normalized vocabulary authoritative, do not change it from this example. Export existing validated usage and ask_delivery_tokens-derived values with source refs, not new independent normalization; distinguish unavailable vs unsupported vs partial. Field evidence and coverage values preserve existing contract. Provider session cumulative measurement cannot be summed per ask. For exact deltas previous and current receipt identity, same compatible generation/model scope and monotone counters required, with measured previous evidence. Usage on failed execution counted when attributable, replay same delivery once. Cache%, quota pp, context estimate and money are separate units; no inferred price or subscription quota. Consumer aggregations use coverage denominator and actual verified task success.

## E. Optional P1 capture

CLI flags proposed on ask only if necessary after owner review: `--capture metadata|content` (default off; validate workspace/source policy), or explicit extension config with owner-approved scope. Existing --json/--final-only shape unchanged when off; when on, prefer separate activity show output, no raw trace in primary response. Do not change existing request-id fingerprint for observational toggle without review; replay never runs a new provider, report original coverage, cannot reconstruct missing trace. Batch must preserve lane boundaries and avoid new writable overlap; direct tool fakes. Optional verification attachment command if concrete need: `peerhub --db DB activity attach ASK_ID --kind test-report --file APPROVED_FILE --json`; owner-authorized write to extension-owned sidecar only, not auto-execution or core mutation. If file attach duplicates Engram, omit and reference its evidence.

Trace observer lifecycle: DISABLED or PLANNED → OBSERVING → COMPLETE/PARTIAL/UNAVAILABLE/CANCELLED. Primary delivery lifecycle independent; no lock order inversion, no slow sink on stdout drain, bounded queue; exceptions caught and separate coverage marker; optional writer may terminate before artifact commit, no terminal change. Only normalized sanitized event batches written, fsync/rename and digest verification; use extension-owned stable locator, immutable committed chunks. Scope revocation blocks read and write. No raw native transcript copy, no hidden reasoning block text, no prompt-secret replay, no auth files, no private paths in public output. Capture approved user-visible conversation content only when explicitly configured; metadata default for enabled mode. Structured event dedup provider version + execution/turn/item/call ID + phase or explicit observed seq; exact duplicate ignored, changed same-key payload conflict or new revision according to schema; final and incremental chunks not separate tasks. Unknown event type/format -> gap not silent successful observation. If provider doesn't expose a tool result/exit, null. User-supplied untrusted records never executed, no prompt injection into policy.

File snapshot contract: approved repo/worktree ID, before/after HEAD and index/worktree digest/status, UTC observed timestamps, path relative/opaque scope ref, change_kind, before/after content hash only permitted, source OBSERVED_GIT/OBSERVED_FILE/PROVIDER_REPORTED, attribution EXACT/AMBIGUOUS/UNKNOWN, coverage with exclusions/limits, report truncated/busy. Path comparison handles case-insensitive collision, NUL-safe Git records and encodings; no symlink traversal or out-of-root reads; before/after distinct dirty modifications. Optional patch ref is content-reviewed/redacted and disabled by default. Git delta and test reports not necessarily caused by AI; exact attribution requires task isolation/explicit evidence.

Test report contract: artifact digest, test_run_id, runner/version, command ref, commit/tree or declared dirty snapshot, observed exit, counts passed/failed/error/skipped/unknown, status PASS/FAIL/INCONCLUSIVE/NOT_RUN, scope and origin. Parse supported bounded JUnit safe XML (no DTD/entity), conflicts/zero tests not PASS. No live test or command execution triggered by import. A self-reported pass is only claim; owner or independent test proof needed for verified task.

## F. Optional P2 manual Web

Candidate namespace, not current CLI:
```powershell
peerhub web prepare chatgpt --prompt-file <approved-prompt> --out <local-packet>
peerhub web receive --packet <local-packet> --reply-file <user-supplied-reply> --json
```
Same for claude/gemini. User manually sends to official site, manually saves reply, explicitly approves each disclosure and local receipt. No network/browser interaction by commands. Packet includes stable random ID, provider label, digest, scope, intended use, created/expiry, permitted text and approved redaction, human instructions; no cookie, account secret, login, automation endpoint. Receipt binds packet digest, reply digest, user-attested role/provider, received_at, source_ref, optional claimed model/conversation ref private, usage null unless authoritative permission/evidence. State PREPARED→USER_ATTESTED_SUBMITTED→RECEIVED or CANCELLED/EXPIRED/UNKNOWN; can import direct receipt with explicit attestation, no fake provider execution. Same ID + digest idempotent; differing reply is explicit correction or conflict, not overwrite. No effect on native ask, session, claims, offsets, automatic route, quota; no wait loop. No live browser automation under this revision. Offline authorized provider archives belong to Engram, not a duplicate PeerHub parser. Review provider terms for downstream storage and reuse, no training/competing use, no unapproved data transfer. Only implement if utility demonstrated; simpler prepare-only or Engram-managed packet acceptable.

## G. Symbolic failures / response rules

INVALID_ARGUMENT, STORE_NOT_FOUND, UNSUPPORTED_SCHEMA, SOURCE_UNAVAILABLE, RESNAPSHOT_REQUIRED, CURSOR_SCOPE_MISMATCH, AMBIGUOUS_LINK, CONTENT_OMITTED, LIMIT_EXCEEDED, CAPTURE_DISABLED, PARTIAL_CAPTURE, TRACE_IO_FAILED, UNSUPPORTED_PROVIDER_EVENT, POLICY_DENIED, EVIDENCE_CONFLICT, WEB_AUTOMATION_UNSUPPORTED, UNKNOWN_USAGE. Reuse actual project error/exit style; map through tests before code. Read page returns no success claim when unsupported/invalid; partial coverage clearly distinct from operational failure. Existing delivered/uncertain semantics unchanged. User-facing guidance gives safe next action, no instructions to bypass provider or repeat uncertain external invocation.
