# Activity export implementation record

## PH-W0, 2026-11-28 KST

PeerHub v0.28.2 main b1d877d689a9bc79d16054de7371b28b108049fd and design fe12b86c058c3c34aca8ded69e2cf2fe4683e451 differ by the design package alone. GitHub listed zero open PRs, open issues 498, 502, 503 and 504, and successful main CI run 45983685529. Both eight-entry manifests passed, including Engram Rev06 at c59c0e42bb6584bc873269b7128a1c94ecba3229. Reviewed the four maintainer documents, source queries, all twelve requirement groups, PHT001–077, PH-01–14 and RSK01–18, and Engram requirements R05/R06, specification §§7–8, contract §5 and T92–100.

F01 probe on a new synthetic database used the normal ask path with FakeRuntimeTarget. One delivery yielded two records and nine evidence rows, including terminal at sequence 6, response_appended at 8 and offset_acked at 9. An independent mark_outcome write committed while a read transaction was open. The reader saw zero outcomes both before and after the write. Its successor saw (1, accepted). Workspace generation existed. Core schema is 4. Records have unique per-stream positions, while bridge evidence and outcomes have separate append-only integer sequences. The caller row includes private paths and process identity. A raw row export is unsuitable.

F02 baseline on Linux, Python 3.12: pytest on test_ask_metrics.py, test_cc_telemetry_events.py and test_flt_bridge.py yielded 113 passed. This includes #502 fresh-session and ag cache semantics, conservative cc handling, late response/ack and restore crash seams. Missing telemetry is reproducible with a scripted terminal lacking a usage candidate. Historical operator coverage rates were not remeasured. No dispatch, parser or accounting repair is justified by this evidence.

## Contract freeze for first RED

Metadata-only export/show with a per-stream record head and independent bridge evidence/outcome heads, frozen until drained. Return a consumer-owned checkpoint at exhaustion for stores with lineage. Bind the token to schema, store, restore generation and epoch, and exact scope. Cap wildcard partitions and require explicit per-stream enrollment above the cap. Legacy lineage permits only a bounded snapshot. Unknown tables, private payloads, missing receipts and incomplete evidence must remain explicit. Decode only allowlisted receipt fields. Feedback and delivery cannot populate a verified task result. The consumer owns logical source enrollment and conflict quarantine. F08 is a producer contract test; Engram runtime agreement requires a separate consumer run.
