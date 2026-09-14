# Executable Discovery & Portable References — Design Reconciliation R1

**Status:** `cc` judgment record reconciling two independent Round-1 drafts
(`ag.deepthink`, `cx.deepthink`) into
`EXECUTABLE-DISCOVERY-AND-PORTABLE-REFERENCES-R1.md`. Documentation-only;
carries no implementation or ratification authority by itself.

## Where ag and cx converged

- `ExecutableReference` as a discriminated union (`ABSOLUTE` |
  `PORTABLE_RELATIVE`) replacing legacy post-hoc path rewriting
  (`_map_subst_drive`/`_relocate`).
- Discovery is strictly read-only: it never auto-writes
  `PeerInstanceConfig`; accepting a candidate requires an explicit
  CAS-guarded `configuration.setting.write`.
- No cwd-dependent resolution anywhere (direct fix for the T71 incident).
- Freeze both the symbolic reference and the concrete resolved binding at
  dispatch time.
- Prefer `PORTABLE_RELATIVE` for candidates under the resolved anchor tree,
  `ABSOLUTE` otherwise.
- New work lives under `configuration.*`, not the frozen legacy 90-action
  vector.

## Where they disagreed, and the resolution

1. **Anchor trust model.** `ag`'s `PORTABLE_RELATIVE` stored an
   `anchor_source` field naming an environment variable directly, trusted
   as-is. `cx` used an `anchor_id` that indirects through a separate,
   versioned, host-owned `AnchorDescriptor` — configuration can select
   *which* anchor to use but cannot define what it resolves to.
   **Resolution: cx.** ag's version is a real, concrete gap, not a style
   preference: if a `PeerInstanceConfig` record (a config-writable object)
   can name *any* environment variable as trusted, anything able to set
   that variable in a child process can redirect executable resolution to
   a different tree while the reference still displays as "portable" and
   passes review. cx's own risk list names this exact failure mode
   ("anchor spoofing"); ag's draft doesn't defend against the risk its own
   design creates.
2. **Path containment.** ag's resolution algorithm canonicalized
   `join(anchor, relative_path)` with no explicit requirement that the
   result stay inside the anchor directory. cx required rejecting `..`,
   absolute/UNC/device-path/ADS forms in `relative_path` up front, and
   verifying after canonicalization (through reparse points) that the
   final physical location still sits within the anchor — rejecting a
   junction/symlink escape. **Resolution: cx.** Under ag's algorithm as
   written, a `relative_path` of `..\..\Windows\System32\cmd.exe` resolves
   outside the anchor with nothing to reject it — a real path-traversal
   gap, not a hypothetical one.
3. **Identity binding / TOCTOU.** cx required physical file-identity +
   digest binding (not path text), deduplication by physical identity, a
   pre-spawn revalidation step, and explicit distinct failure states for
   "changed between discovery and confirmation" vs. "changed between
   resolution and spawn." ag's evidence shape included an identity hash
   field but specified neither pre-spawn revalidation nor the
   discovery-to-confirmation staleness window. **Resolution: cx** — kept
   in full, including both staleness failure codes
   (`DISCOVERY_CANDIDATE_STALE`, `EXECUTABLE_BINDING_STALE`).
4. **Discovery-spec ownership.** ag's scanner implicitly held portable-tree
   location knowledge itself. cx made this an explicit per-adapter
   `ExecutableDiscoverySpec` declaration. **Resolution: cx** — this is not
   a new call; it is the same "core never branches on peer identity"
   invariant already converged in `ARCHITECTURE.md` §7, which ag's draft
   didn't carry forward into this new area.
5. **Document split.** Both proposed a single combined document here
   (unlike the earlier quota/settings round, where `cx` argued for a
   split) — no reconciliation needed; `cx`'s more granular 11-section
   outline was used as the base structure since it already matched the
   final content depth.

## Assessment

Every substantive disagreement in this round resolved in `cx`'s favor, and
each one is a concrete correctness/security gap in `ag`'s draft rather than
a matter of taste (see points 1-2 above in particular: an unvalidated
anchor-trust field and a missing path-containment check are exactly the
kind of defect this document's own fixture list in §10 exists to catch).
`ag`'s discriminated-union framing, dispatch-freeze sketch, and
preference-rule logic (§8) were sound and are retained.

## Final Call

Sent to both `ag` and `cx` before commit, per the R:10 consensus protocol
(`protocol.json.consensus.r10_voters = [cc, ag, cx]`), including the
specific security-gap findings in points 1-2 above so `ag` can concretely
object if this reconciliation misread its draft.

- **`ag.deepthink`: ACK.** Confirmed both gap characterizations (anchor
  indirection, path containment) as real and correctly described.
- **`cx.deepthink`: ACK-WITH-CONCERN.** Flagged that §7's "immediate
  pre-spawn revalidation" is not itself TOCTOU-safe for pathname-based
  process creation: a replacement can still occur after the revalidation
  check but before the OS acquires the process image, since re-checking a
  path and spawning from that same path are two separate operations with a
  race window between them. **Concrete and correct; fixed**: §7 now
  requires retained object/namespace custody of the validated executable
  through image acquisition (e.g. spawning from an already-open handle
  obtained at validation time, not a path re-resolved at
  `CreateProcess`/`exec` time), and a new failure code,
  `EXECUTABLE_IDENTITY_UNPROVABLE`, added to §6's table and §10's fixture
  list, for platforms/entrypoint kinds that cannot provide an equivalent
  same-image guarantee.

Both peers are unanimous on the resolution as corrected. Neither original
draft is discarded — both remain in IPC session history as independent
design evidence; this record is the additive synthesis, not a rewrite of
either.
