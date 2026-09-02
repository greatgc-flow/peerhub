---
status: REFERENCE
date: 2026-09-02
title: Session Lessons Index — where each hard-won pattern actually lives
---

# Session Lessons Index

**Purpose.** `docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md` is the
chronological implementation log for the 2026-08-27 → 2026-09-02 TDD
marathon, and it already contains nearly every real lesson from that work —
each dated round entry records not just what shipped, but what went wrong
and why, in detail, with file/line citations. What it does *not* have is an
index: finding "the pytest ACL workaround" or "the concurrent-write
collision" means reading roughly 600 lines top to bottom. This doc is that
index. It does not restate the lessons — each row links to the section that
already has the full account, so there is exactly one place each lesson's
substance can drift out of date.

Do not duplicate a lesson's substance into this file when adding a new row.
Write the real account in `HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md` (or a
dedicated design doc for anything design-shaped), then add a one-line
pointer here.

## Peer-dispatch reliability

| Lesson | Where |
|---|---|
| A "failed"/`execution_state=uncertain` dispatch can still have written complete, correct work — `git status` is ground truth, not the hub's own status report. Recurred on `peer-registry`, `model-status`, `broker-status`, and Gap 6 capability matching. | "`peer-registry` implementation round" and every "Round (2026-09-0x): ... backed" entry that opens with "reported failed/uncertain" |
| A killed dispatch's partial output is worth reading closely, not compiling-checking. `py_compile`/syntax validity proves nothing about imports resolving, methods existing, or matching this codebase's real conventions — only actually importing, running against real infra, and grep-comparing against a working precedent catches those. | "`peer-registry` implementation round: another killed dispatch..." (the "Running lesson across this session's last three killed dispatches" paragraph) |
| A peer dispatch failure isn't always the peer's fault — check the environment (e.g. a stray `.git/MERGE_HEAD` in an unrelated outer sandbox repo) before assuming a fresh regression or re-diagnosing from scratch. | "Health/admission/routing scouting + `peer-registry` ratification round" (Lesson 1) |
| Detailed dispatch prompts that cite exact existing sibling code (file:line, exact signatures, exact conventions to mirror) land dramatically cleaner than open-ended "implement X" prompts. | Visible across every round from `thread-new` onward; most explicit in the Gap 6 round's dispatch-prompt structure |
| Two-peer research-then-critique catches real, sometimes counterintuitive bugs research alone misses — including backwards assumptions (`challenge_until` *permits* a challenge, doesn't protect the incumbent) and scope creep before it ships (`activate_pending_leadership()` dropped after a single grep proved no legacy caller exists). | "Fixing `leader-claim`/`leader-yield` for real, overnight", "The 'Health Cluster' Investigation", "Capability Matching and Leader Election" |

## Codebase-specific bug classes

| Lesson | Where |
|---|---|
| Inserting a new `@dataclass(frozen=True, slots=True)` Command class immediately adjacent to an existing one has repeatedly caused the decorator to duplicate onto the wrong class or vanish — always `python -c "import peerhub"` after the insertion. | "Lesson Inject Design Round" implementation note (missing dataclass decorator) |
| Changing one dataclass's fields can silently break unrelated commands that inherit their shape from it via plain Python class inheritance, not composition. | "Fixing `leader-claim`/`leader-yield` for real, overnight" (`TerminalHeartbeatCommand`/`TerminalCloseCommand` inheriting from `LeaderYieldCommand`) |
| A yield/clear/reset operation must null *every* stale field it introduced, not just the primary state flag — "actively misleading residue" recurred twice in the same round (`challenge_until` in critique, `reason` at implementation). | "Fixing `leader-claim`/`leader-yield` for real, overnight" (final paragraph) |
| A test-collection basename collision (`tests/unit/test_x.py` vs. `tests/integration/.../test_x.py`) causes an `import file mismatch` under this project's no-`__init__.py` pytest layout — rename one file. | "Round (2026-09-02): Gap 6 capability matching implemented" |
| `cx`'s own sandboxed pytest hits a Windows temp-directory ACL restriction in most rounds — it can `pyright`-check but usually can't execute its own tests. Always re-run the real suite from the terminal's unrestricted environment; use `--basetemp=<repo>\.pytest_tmp_*` to work around the ACL issue directly. | "Verification discipline (why every commit's message says 'independently verified')" |
| A fake/stub in a test should echo the real input back (`request.profile_id`), never hardcode a literal that happens to match today's implementation — two hardcoded literals (production default + test double) can drift together and hide a real mismatch indefinitely. | "Health/admission/routing scouting + `peer-registry` ratification round" (Lesson 2) |
| Two peers dispatched to overlapping shared command-bus files (`legacy.py`/`api.py`/`cli.py`) at the same time can silently drop one dispatch's entire insertion with zero corruption signature — only caught via a pytest collection `ImportError`. Never dispatch two peers to those files concurrently; re-grep for every symbol an earlier round added before trusting `git diff --stat` alone. | "Round (2026-09-01): 6 health/admission quick wins backed" |
| Byte-level corruption check (null bytes, stray control bytes outside `\t\n\r`, literal `??` mojibake markers) runs on every touched file before every commit. Known false positive: prose that mentions "`??` corruption markers" inside this very progress doc's own verification write-ups. | Repeated in every round's "Verified:" paragraph from `arbiter-review` onward |

## Design discipline

| Lesson | Where |
|---|---|
| "No architecture, no implementation" — a design correctly refused to ship when real prerequisites were missing (no capability catalog, no non-synthetic-zero evidence source for 5 scoring factors), rather than shipping something plausible-but-wrong. Gap 6 sat blocked for three days until those prerequisites were actually resolved. | "Capability Matching and Leader Election: A blocked design proving the value of the 'No architecture, no implementation' rule" |
| "Verify, don't assume completeness" — a single exhaustive grep across all of `hub.py` for every write of `leadership.status = "ACTIVE"` found exactly one site (belonging to a *different* action), proving an entire assumed lifecycle transition didn't exist and preventing it from being invented and shipped. | "Fixing `leader-claim`/`leader-yield` for real, overnight" (critique paragraph) |
| A rejection of a design/implementation match should be corrected in place when it turns out wrong, with what was actually wrong (the specific service) vs. right (a new domain) stated explicitly — not left standing next to a superseding note. | `docs/design/PEERHUB-BACKLOG-2026-08-27.md`, the `artifact-claim`/`file-lock` correction |
| The `LEGACY_CATALOG` count has a known, self-consistent counting-formula nuance: `ask`/`ask-all`/`ask-coordinator` have real `LegacyTranslator` branches but their target `dispatch.submit*` methods are never registered as real `CommandDescriptor`s, so they don't execute end-to-end despite a raw branch-count grep including them. This is bookkeeping, not an open action item. | "Bookkeeping note, unrelated to tonight's work, found while recounting" (`PEERHUB-BACKLOG-2026-08-27.md`) |

## Environment / tooling (outside peerhub's own code)

| Lesson | Where |
|---|---|
| The four recurring `P:\`/hub.py-layer infra incidents (drive-rename path resolution, cx sandbox DPAPI, and two more) — read before re-diagnosing any of the symptoms from scratch. | `docs/design/OVERNIGHT-INFRA-LESSONS-2026-08-10.md` |
| `git status`/`git clean`-based cleanup can be fully blind to real, non-empty scratch directories on disk. gitignore directory-name patterns (`.peerhub/`, `__pycache__/`) match at *any* depth, and git only ever tracks/lists files, never directories — so a one-off scratch directory whose sole contents happen to sit under a path component matching an ignored directory name (e.g. `.some-test-dir/.peerhub/peerhub.sqlite3`) is invisible to `git status` even though real bytes sit on disk, sometimes for days. A truly empty directory (0 files) is separately invisible for the same underlying reason (git never lists empty directories at all). Found 2026-09-02 while auditing whether the repo root was actually clean after an earlier `git status`-driven cleanup pass — it wasn't; dozens of `.codex-*`/`.manual_*`/`.debug-runtime*`/`.alert-*` directories from throughout the session remained. **Fix for a future cleanup pass: enumerate the filesystem directly (`os.scandir`/`find`), not `git status`, whenever the goal is "remove everything not meant to be here."** | This entry — not yet a dedicated round in the TDD-progress doc |

## What this index deliberately does not cover

- **Design rationale** for any shipped domain (schema, invariants, why one architecture beat another) — that's `docs/design/PEERHUB-BACKLOG-2026-08-27.md`'s job, organized by domain, not chronology or lesson-type.
- **Current implementation status** (what's backed, what's waived, what's still open) — that's the top of `PEERHUB-BACKLOG-2026-08-27.md`'s "Where things stand right now" section, and the summary in `README.md`.
- **This assistant's own cross-session operating memory** (peer quota-routing behavior, IPC dispatch mechanics, this specific portable-environment's own quirks) — that lives outside this repo, in the assistant's own memory store, and is not reproduced here since it isn't repo-portable knowledge a human or another peer reading this repo would need.
