# PeerHub activity export 1.0

The producer contract ID is `peerhub.activity/1.0`. This feature branch supports an explicitly enrolled, local PeerHub v0.28.2 store at Core schema 4. Engram Rev06 §5.2 is a design sketch. Bind the adapter to the actual transport keys below and to the [synthetic exchange](engram-v1.json).

## Read commands

```shell
peerhub --db /approved/core.db activity export --stream review --limit 100 --json
peerhub --db /approved/core.db activity export --stream review --cursor TOKEN --json
peerhub --db /approved/core.db activity show PROMPT_RECORD_ID --stream review --limit 100 --json
peerhub --db /approved/core.db activity show PROMPT_RECORD_ID --stream review --cursor TOKEN --json
```

Use an argument vector and supply the database explicitly. Enrollment, consent version, approved projects, export recipients and storage policy belong to the caller. Revalidate them on every page. An agent with one enrolled stream must always send that stream filter. Use the same `--stream` on `show`. An out-of-scope prompt and an unknown ID both return ASK_NOT_FOUND. Sharing a database login gives no per-user access control. The exporter reads local routing identifiers, which can themselves contain identifying text. Review those identifiers before sharing outside the enrolled project.

V1 provides metadata only. A prompt and its response have separate record IDs, actor, position and exact `reply_to`. A bridge-authored response also carries its proven delivery key. Prompt bodies, response bodies, free-form errors, caller details, native session IDs, tool arguments and workspace paths are omitted. `--include-content` is outside this version. An authorized consumer can use PeerHub's existing single-stream record reader under its separate content policy. No new trace or provider contact is required.

## Envelope and identity

| Transport field | Engram mapping and rule |
|---|---|
| `schema_version`, `source` | Accept exactly `1.0` and `peerhub` before ingesting the page. |
| `store_ref`, `lineage` | Local cursor fences. Engram assigns the persistent logical `source_instance_id` on enrollment. Confirm moves, clones and restored source identity before mapping them to an existing enrollment. |
| `items[]` | Rev06 draft `events[]`. Items have `source_event_id`, `source_revision` (string `1` for immutable facts), `source_digest`, `kind`, `stream_id`, `record_id`, and nullable `occurred_at`. Observation times can be absent. Consumer supplies ingest time. |
| `source_digest` | Full SHA-256 of the item excluding this field. Serialize UTF-8 JSON, sorted keys, no ASCII escaping, no whitespace, finite numbers. It covers the permitted projection. |
| `channels`, `coverage` | Availability of record, evidence and outcome channels is independent. Overall coverage is partial. Content is omitted, semantic task verification is unknown. Missing telemetry, counters or occurrence times are unknown. |
| `next_cursor`, `has_more` | Continue the frozen inventory when true. When false, commit the non-null cursor and poll it for a fresh set of heads. A metadata snapshot with no lineage has a null cursor. |

Record keys use the immutable Record ID. Evidence and outcome keys use their own append-only sequence. Channel order is deterministic within a frozen cycle: records in stream/position order, then evidence sequence, then outcome sequence. This ordering does not establish a global occurrence time. Same enrollment + event ID + revision + digest means duplicate. A different digest for the same key requires quarantine. Keep the previous evidence. Explicit source restoration can orphan old facts. Reconcile them with the restored source before using them for current evaluations.

## Evidence and coverage

| Kind | Proven facts |
|---|---|
| `ai.prompt`, `ai.response`, `peer.record`, `peer.reconciliation` | Historical Record, author, single target when known, redacted body locator via Record ID, exact reply and applicable request/delivery ID. |
| `peer.evidence` | Append-only phase, attempt, delivery key and original certainty. Invocation number and opaque execution ref when recorded. Only an actual `offset_acked` yields `acked=true`. At earlier phases this field is null. Reconcile registration links the authorization Record. Unknown phase names become `unclassified`. |
| `peer.session` | An immutable session call receipt provides public handle, generation and recorded fresh/resumed mode. No vendor session token or mutable latest session summary is serialized. |
| `peer.usage` | Validated v3 session telemetry or a validated delivery-scoped API usage observation on runtime failure. `usage.reported`, `normalized`, `source`, `scope`, `coverage`, `attribution` and `field_evidence` follow PeerHub's canonical usage contract. Current writers leave field_evidence empty. |
| `peer.outcome` | Every user mark (accepted, rejected or retried) gets its own event key. Feedback records adoption. Independent proof belongs to the consumer. |
| `coverage.gap` | A projected item exceeded the detail limit. The consumer can record the omission and continue. |

A terminal event, response materialization and acknowledgement may arrive on separate polls. A settled incomplete or refused attempt can be STARTED and acknowledged without a response. Bounded settlement fields distinguish persisted refusal, incomplete/truncated, denied-empty and selector failure when available. A null status records the absence of an established ask status in this projection. Delivery certainty and acknowledgement remain separately observable. Neither replay nor recovery executes through this reader.

CC, CX and AG receipts measure cumulative session counters. CA, OA and GA measure a delivery. Use `delivery_measurement` for per-delivery fields. A first receipt with a proven fresh session can use the existing zero baseline. Other cumulative receipts require a compatible immediately prior delivery and an eligible earlier telemetry receipt. `baseline_evidence_seq` names that receipt. Counter resets, gaps and incompatible provider/session/binding measurements yield null for the affected fields. Reported zero stays zero. Deduplicate evidence keys and count a delivery once. Never add raw cumulative counts to their derived deltas. Token counts do not establish cost, quota, context occupancy or task success.

## Snapshot, limits and failures

One read-only SQLite transaction per page captures per-stream Record positions and independent evidence/outcome sequence heads. No cursor or collaboration Offset is written at source. A stream that sorts earlier is included on the next poll. Store ref binds resolved locator and filesystem identity. Lineage hashes the existing workspace generation and restore epoch. Managed restores or a physical clone invalidate old cursors. Unmanaged in-place rewrites that preserve file identity, markers and sequence heads require an explicit reenrollment/resnapshot. A cursor's checksum detects accidental damage. It is not a credential. Store it privately as it includes enrolled stream identifiers.

| Limit / status | Consumer action |
|---|---|
| 1–200 items (default 100), 512–1,048,576 wire bytes (default maximum) | Apply both bounds. On `LIMIT_EXCEEDED`, increase the byte budget and retry the previous committed cursor. |
| Up to 32 wildcard partitions; identifiers for stream selection and prompt lookup up to 256 UTF-8 bytes | Above the partition cap, enumerate `stream list` from its beginning each discovery cycle. Intersect with enrollment, then keep one `--stream` checkpoint per authorized stream. Deduplicate previous wildcard pages during transition. |
| `NO_LINEAGE` | Only a bounded historical page is supported. If `has_more` is true it is incomplete. For larger legacy sources use the approved record-reader fallback and report partial late-event coverage. |
| `INVALID_CURSOR`, `CURSOR_SCOPE_MISMATCH`, `RESNAPSHOT_REQUIRED` (exit 2) | Recover the token for the original scope or restart an authorized snapshot. Preserve existing evidence for digest reconciliation. |
| Missing explicit selection (2), missing store (5), unsupported schema (6), unknown prompt (1) | Correct the enrollment or feature-detect the Phase A fallback. Never create a store or replay a provider call. |
| Malformed/busy/unavailable source or oversized SQLite row (5) | Keep the previous checkpoint. Use a narrower approved scope or a consistent authorized backup. Old databases with absent bridge/outcome tables can provide partial channels. |
| Resources | The live connection uses mode=ro, query_only, short lock wait and a SQLite progress deadline. Output is assembled after bounded item selection. SQLite can use transient WAL/SHM sidecars. A slow consumer holds no read transaction across pages. |

Commit the page's accepted events, quarantined conflicts and checkpoint atomically in the Engram ledger. A crash before commit repeats the same page. After commit, continue from the stored checkpoint. Use the published vector's first, drain and late pages to test retries, late feedback, natural keys and digest verification. The workspace/lineage labels in the vector are fixed synthetic test inputs. Engram's native CLI importer remains available independently.
