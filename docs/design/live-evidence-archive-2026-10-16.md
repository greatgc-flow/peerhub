# Live Evidence Archive Design — 2026-10-16

**Status:** Deferred (Separate PR series following monitor detail expansion).  
**Review base:** `c717ee0097c7a1d89fa8d72616207d0dd0db7588` on `fix/monitor-detail-expansion`.

## Scope and Intent

Consolidate individual date-named canary receipts (`YYYY-MM-DD.json`) into a single canonical archive file at `docs/implementation/live_evidence/archive.json`. This change is purely storage-focused, does not spend provider quota, and is deferred to an independent PR series following monitor detail expansion.

The nine existing receipts (2026-10-03 through 2026-10-16) remain intact until migration validation, tests, and reference updates succeed.

## Archive Schema and API Contract

```json
{
  "schema": "peerhub-m1-live-evidence-archive/1",
  "runs": {
    "2026-10-16": {
      "schema": "peerhub-m1-live-evidence/1",
      "updated_at": "2026-10-16T05:20:47+00:00",
      "providers": {}
    }
  }
}
```

- `evidence_path(run_date=None)` returns `ARCHIVE_PATH` (or honors environment path overrides such as `PEERHUB_LIVE_EVIDENCE_PATH`).
- `load_evidence(path=None, run_date=None)` returns the selected run's payload. A missing date defaults to today's pinned UTC date. If the run is absent, it returns an empty run shape.
- `merge_evidence(kind, section, entry, path=None, run_date=None)` updates only the specified provider section for the given run date, preserving all other dates and provider entries.
- Unrecognized or malformed archive schemas are rejected immediately; writers must never overwrite or truncate an unparseable archive.

## Lifecycle and Migration Contract

1. **Pinned UTC Date:** Invocations pin `datetime.now(timezone.utc).date()` at execution start, preventing day-boundary rollover during multi-provider runs.
2. **Path Overrides:** Respect environment variables and explicit path arguments in all loader and merger entry points.
3. **Idempotent Migration:** Migration scans existing `YYYY-MM-DD.json` files and performs a locked union of absent dates into `archive.json`. If a date already exists with identical content, migration is a no-op; if conflicting unequal payloads exist for the same date, migration aborts with an error.
4. **Validation Gate:** Existing receipts are removed only after offline test suites validate round-trip loading and documentation references (`STATUS.md`, `HANDOFF.md`, `index.md`) are updated.

## Concurrency Decision (To Be Settled in Future PR Debate)

The locking mechanism remains open and will be decided during that PR's dedicated review:
- **CX Position:** Retain OS-level byte locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) on a sidecar lock file to serialize read-modify-write cycles across concurrent test runs (e.g., pytest-xdist or overlapping gate runs).
- **AG Position:** Drop OS byte locking and sidecar files; canaries are strictly sequential developer commands excluded from CI, and existing atomic temporary file replacement (`replace_with_retry`) provides sufficient safety without untracked lock artifacts.
- Both peers agree that whatever lock model is chosen, writers must flush to a sibling temporary file and atomically replace the target.
