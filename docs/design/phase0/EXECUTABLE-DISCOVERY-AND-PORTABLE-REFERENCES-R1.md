# Executable Discovery and Portable References R1

**Status:** Proposed Phase 0 design addition — design-only. Explicitly
excludes installation, download, authentication, automatic configuration,
database creation, and live mutation.

**Scope:** How PeerHub finds an already-installed peer CLI on disk without
installing anything, and how a configured executable reference survives
this portable environment's drive letter changing between mounts, without
adopting the legacy pattern of rewriting every stored absolute path after
the fact (`_map_subst_drive` / `_relocate`).

## 1. Ownership and non-authorization boundary

Discovery hints belong to adapter/host descriptors. Scanning and resolution
are generic `readiness` infrastructure, not adapter-specific code — core
never branches on `if peer == "cx"` (existing invariant, `ARCHITECTURE.md`
§7). The configured choice remains sole property of `PeerInstanceConfig`.
A discovered candidate is immutable evidence, never configuration:
discovery MUST NOT write `PeerInstanceConfig` under any circumstance. This
document reaffirms the permanent vendor boundary: PeerHub discovers
existing binaries; it never installs, updates, or authenticates them.

## 2. Executable reference and anchor model

```text
ExecutableReference =
  ABSOLUTE { path }
  | PORTABLE_RELATIVE { anchor_id, relative_path }
```

`anchor_id` is a symbolic key, **not** an arbitrary environment-variable
name supplied by configuration. A versioned, host-owned `AnchorDescriptor`
maps each `anchor_id` to exactly one trusted launcher-supplied source (e.g.
a portable-root variable the launcher itself sets at start-up). A
`PeerInstanceConfig` record can select *which* `anchor_id` to use; it
cannot mint or redefine what that id resolves to. This closes a concrete
anchor-spoofing gap: if configuration could name any environment variable
directly, anything able to set that variable in a child process could
redirect executable resolution to a different tree while the reference
still displays as "portable."

`PeerInstanceConfig` stores only the symbolic reference — never a cached
resolved absolute path as a second configurable field.

## 3. Deterministic discovery context

A generic `readiness.discovery` service receives a sealed
`DiscoveryContext`: platform + architecture, sanitized **absolute** PATH
entries, `PATHEXT` snapshot, trusted anchor bindings, the adapter
discovery-spec revision, and the resolver revision.

Prohibited unconditionally: `shutil.which`, `where.exe`, any shell search,
implicit parent-directory walking, unbounded recursive drive scans, and any
resolution against the caller's current working directory. This is the
direct fix for the T71 incident (2026-07-19), where a cwd-dependent
`shutil.which()` call latched peers into a false `RED`/`cli_not_found`
state depending on which directory the caller happened to be in. Empty or
relative PATH entries are ignored rather than silently treated as cwd.

## 4. Candidate collection and identity evidence

Each adapter declares its own discovery surface — not a hardcoded branch in
core scanning code (the same "no peer-specific core branches" invariant as
§1):

```text
ExecutableDiscoverySpec
  adapter_id
  candidate_names              # codex.cmd, claude.exe, agy.exe...
  allowed_entrypoint_kinds     # EXE, CMD_SHIM, SCRIPT...
  portable_relative_hints      # _sys/env/nodejs/npm-global, tools/<peer>...
  identity_probe               # optional, declared no-provider-effect probe
  descriptor_revision
```

Scan algorithm: enumerate exact candidate names under each absolute PATH
entry, in PATH order; enumerate only descriptor-declared locations under
resolved portable anchors; canonicalize every candidate through reparse
points and deduplicate by resolved volume/file identity, not case-folded
path text; optionally run a bounded identity/version probe only when the
adapter declares it local, non-authenticating, non-provider-effect, and
non-quota-consuming.

A discovered file is not yet a valid configured executable. Candidate
states are `FOUND_UNPROBED`, `IDENTITY_VERIFIED`, or `REJECTED` — absence
of a safe probe leaves a candidate unverified, never guessed valid.

```text
ExecutableDiscoveryCandidate
  candidate_id
  adapter_id
  proposed_reference            # ABSOLUTE or PORTABLE_RELATIVE, per §6
  resolved_absolute_path
  resolved volume/file identity
  entrypoint digest + length
  wrapper/interpreter identity, if applicable
  discovery_method: PATH | PORTABLE_HINT | HOST_HINT
  discovery-context revision
  identity-probe method/result/version
  observed_at + evidence freshness
```

For a `.cmd`/script shim, hashing only the wrapper is insufficient if
execution depends on a separately resolved interpreter or target — the
evidence must bind the whole launch chain that readiness actually
validated.

## 5. Explicit confirmation through configuration

Discovery is exposed as a read/evidence command,
`configuration.executable.discover`. It is a new PeerHub
`configuration.*` command, not an addition to the frozen legacy 90-action
inventory (`ACTION-INVENTORY-RECEIPT-R1.md` binds that vector's hash;
extending it is out of scope and would need its own additive, ratified
change).

Accepting a candidate is always an explicit, separately confirmed
`configuration.setting.write` against `PeerInstanceConfig`, carrying the
candidate's identity/digest, the config's expected CAS revision, an actor,
and a reason. A repeated scan can never mutate configuration by itself.

## 6. Portable resolution algorithm

At session or dispatch planning:

1. Load the frozen `PeerInstanceConfig` revision and its symbolic
   reference.
2. `ABSOLUTE`: require an absolute path; never reinterpret it relative to
   cwd.
3. `PORTABLE_RELATIVE`: obtain the named anchor from the trusted launcher
   context via `anchor_id` (§2) — never inferred by walking parent
   directories or scanning drive letters.
4. Require the anchor to be absolute and an existing directory;
   canonicalize it through OS handles/reparse points and record its
   physical identity.
5. Reject a `relative_path` containing drive prefixes, UNC roots, device
   paths, alternate data streams, or `..` traversal.
6. Join anchor and relative path, canonicalize the target, and verify its
   final physical location remains within the canonical anchor. A junction
   or symlink that escapes the anchor fails resolution — path text alone
   is not sufficient evidence of containment.
7. Require the target to be an adapter-allowed entrypoint kind and bind its
   file identity, digest, length, and any wrapper/interpreter chain.
8. Produce an immutable `ResolvedExecutableBinding`; readiness probes that
   exact binding, never a re-derived path.

```text
ResolvedExecutableBinding
  symbolic_reference + digest
  PeerInstanceConfig revision
  anchor_id + anchor-source revision
  raw/canonical anchor value used
  anchor physical identity
  resolved absolute executable path
  executable physical identity + digest
  resolver revision
  readiness receipt reference
```

This deliberately avoids `_relocate`-style rewriting: a drive move changes
the *runtime anchor binding*, not every persisted configuration record —
unlike the legacy `_map_subst_drive`/`_relocate` mechanism, which detects a
move after the fact and patches stored absolute paths across peer configs.

### Failure semantics

| Condition | Error |
|---|---|
| Anchor unset | `EXECUTABLE_ANCHOR_UNAVAILABLE` |
| Anchor non-absolute or nonexistent | `EXECUTABLE_ANCHOR_INVALID` |
| Relative target missing | `EXECUTABLE_UNAVAILABLE` |
| Target escapes anchor | `EXECUTABLE_REFERENCE_ESCAPES_ANCHOR` |
| Candidate changed between discovery and confirmation | `DISCOVERY_CANDIDATE_STALE` |
| Candidate changed between resolution and spawn | `EXECUTABLE_BINDING_STALE` (no process starts) |
| No same-image guarantee available through acquisition (§7) | `EXECUTABLE_IDENTITY_UNPROVABLE` (no process starts) |
| Multiple distinct anchors bound to one `anchor_id` | `EXECUTABLE_ANCHOR_AMBIGUOUS` (requires explicit host/user correction) |

Multiple *textual* anchors resolving to the same physical directory may be
deduplicated as aliases, but the selected launcher source and its aliases
remain audit evidence. No failure here falls back to cwd, PATH, another
drive, or "the first plausible candidate."

## 7. Dispatch and session binding

Freeze both representations at dispatch time: the symbolic
`ExecutableReference` (explains configured intent) and the concrete
`ResolvedExecutableBinding` (proves what was actually launched).
Immediately before spawn, revalidate the config revision, anchor-source
revision, and executable physical identity; a mismatch triggers re-plan
without starting a process.

Pathname-based revalidation alone is not TOCTOU-safe: a replacement can
still occur after the immediate-pre-spawn check but before the OS actually
acquires the process image, since re-checking a path and then spawning
*from that same path* are two separate operations with a race window
between them. Spawn MUST use retained object/namespace custody of the
validated executable through image acquisition — e.g. spawning from an
already-open handle/descriptor obtained at validation time, not by
re-resolving the pathname at the moment of `CreateProcess`/`exec`. If the
platform or entrypoint kind cannot provide an equivalent same-image
guarantee, the dispatch MUST fail closed as `EXECUTABLE_IDENTITY_UNPROVABLE`
rather than spawn from a re-resolved path.

If the anchor changes after a process has already spawned, that in-flight
process continues under its frozen process/executable evidence — PeerHub
does not rewrite the attempt or kill/replay it merely because the drive
letter changed mid-flight. Any resulting process failure is recorded
normally. A new dispatch resolves the new anchor and requires fresh
readiness. Existing persistent sessions are not automatically reusable
across an anchor/binding change — session resume must match the new
executable/readiness fingerprint or a new session is created. An uncertain
attempt is never retried solely because a remount was observed.

## 8. Candidate reference preference

Discovery recommends `PORTABLE_RELATIVE` when the final resolved
executable and its full required launch chain are safely contained under
exactly one trusted anchor; the evidence still records the absolute path
used during discovery. It uses `ABSOLUTE` when the candidate is outside
every anchor (e.g. a machine-level npm installation) — a valid explicit
choice, worth surfacing as machine-bound in monitoring but not rejected.

Discovery method and reference kind are independent: a PATH-discovered
executable that happens to sit inside the portable tree still produces a
relative proposal. Conversely, a symlink inside the tree whose final
target escapes it must never be represented as portable merely because its
path text has the right prefix (§6 step 6).

## 9. Security, privacy, and SSOT constraints

- **Candidate/config conflation:** persisted discovery evidence must never
  become an implicit configured choice.
- **Resolved-path duplication:** the absolute result belongs to
  readiness/attempt evidence, not another writable `PeerInstanceConfig`
  field.
- **Anchor spoofing:** arbitrary child-process environment values cannot be
  trusted anchor authority (§2).
- **Core layout coupling:** portable-tree locations belong in host/adapter
  discovery descriptors, not hardcoded peer-specific branches in core.
- **PATH hijacking:** PATH order is evidence, not trust; user confirmation
  and identity probing remain required regardless of PATH position.
- **TOCTOU:** confirmation and pre-spawn checks compare physical
  identity/digest, not path text alone.
- **Wrapper ambiguity:** a stable `.cmd` file can launch a changed
  interpreter or package target; bind the validated launch chain, not just
  the wrapper file.
- **Alias ambiguity:** drive letters, `subst`, junctions, case aliases, and
  short (8.3) names can name the same object — deduplicate by physical
  identity.
- **Monitoring side effects:** observing discovery/configuration must
  never rescan, execute a version probe, or refresh readiness unless a
  separate, explicit discovery/probe command is invoked.
- **Protocol drift:** these are additive `configuration.*` commands, never
  new entries in the ratified legacy 90-action vector.

## 10. Required pre-TDD fixtures

cwd-independence; relative/empty PATH entry rejection; portable drive-letter
change (anchor value changes, no config rewrite); `subst` alias
deduplication; two distinct anchors bound to one `anchor_id`
(`EXECUTABLE_ANCHOR_AMBIGUOUS`); same-physical-identity aliases; junction/
symlink escape rejection; stale candidate between discovery and
confirmation; executable replaced between resolution and spawn; executable
replaced in the window between pre-spawn revalidation and image
acquisition (must fail `EXECUTABLE_IDENTITY_UNPROVABLE`, not spawn from a
re-resolved path); wrapper launch-chain target drift; anchor change during
an already-spawned attempt (in-flight process unaffected).

## 11. Ratification gate

This document, the relevant `ARCHITECTURE.md` revision, the settings-surface
revision (`UNIFIED-SETTINGS-SURFACE-R1.md`), the reference/binding schemas
in §2/§6, the resolver rules in §6, and the fixture matrix in §10 must be
bound together by hash in a new unanimous Hub round before any of this
becomes buildable. Ratification authorizes documentation semantics only —
not implementation, database creation, or live configuration mutation, and
remains gated by `TDD-READINESS-GATE-R1.md`.

## 12. Provenance

Drafted independently by `ag.deepthink` and `cx.deepthink` from a shared
brief (2026-07-29), reconciled in
`EXECUTABLE-DISCOVERY-RECONCILIATION-R1.md`. The core discriminated-union
concept and mid-session-remount sketch were convergent between both
drafts; this document follows `cx`'s stricter anchor-trust indirection and
path-containment model where the two diverged (see the reconciliation
record for why).
