# Legacy import scope (M1 migration guide)

The legacy v0.x store is migrated PARTIALLY by `python -m peerhub.m1_cli legacy-import` (module `peerhub/m1/legacy_import.py`, TD-16, D-W7-1). This is a
deliberate, owner-accepted scope (B6): only what has a defined M1 meaning is imported; the rest is reported and stays where it is.

## Imported
| Legacy table | M1 result |
|---|---|
| `event_log` | Records of Stream `legacy:<correlation_id>`, author Peer `legacy:orchestrator`, kind `legacy.event`, body = every event_log column (JSON columns parsed), `created_at` = `occurred_at` epoch seconds |
| `consumer_offsets` | Peer `legacy:consumer:<consumer_id>` plus one Offset per Stream = number of that Stream's imported Records at or before the consumer's legacy outbox position |

## Reported as unmapped (NOT imported; they stay in the legacy database)
Every other non-empty table: dispatch history (`dispatch_*`), leases, governed targets, and anything else. Nothing is guessed or converted.
M1 has no equivalent of an in-flight dispatch, a lease or a governed target, so importing them would invent state.

## Partial-migration statement
After an import the M1 workspace contains the event history and consumer positions only. The legacy database is opened read-only/immutable
and is never modified; it remains the system of record for the unmapped data. Do not delete or archive it until the unmapped report is
empty or you have accepted each listed table. Re-running the import is idempotent (persisted rows are compared; M1 Records appended
later are tolerated); foreign or divergent rows are reported as `conflict` and never touched; a malformed row isolates its whole
correlation group (`malformed`) while other groups import.

## How to run and read the report
1. `python -m peerhub.m1_cli --db <m1.db> legacy-import dry-run --source <legacy.db>` writes nothing (the target is not even created) and prints a JSON report.
2. Check `plan_digest`, then `legacy-import apply --source <legacy.db> --plan-digest <digest>` to refuse if the source changed since the dry-run (exit 7 on refusal).
3. Report fields: `units` (per correlation group: status imported / already_imported / conflict / malformed, with reasons), `totals`,
   `malformed_consumers`, `mappings` (what is declared mapped), and **`unmapped_tables`**: a map `table -> row count` of everything that
   was NOT migrated. A non-empty `unmapped_tables` is expected for a real legacy store; it is the list of data that still lives only in legacy storage.
4. `source_sha256` identifies the exact legacy file the report was computed from.
