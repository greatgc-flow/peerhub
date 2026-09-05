# Final Adversarial Security Review
## PHASE1-MANIFEST-SCHEMA-V2-2026-08-20 — Adapter-Manifest Admission Design

> [!IMPORTANT]
> **This is the required ratification-gate review** referenced by
> `docs/design/PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` and the
> Engram-side deferral note
> (`_sys/data/sessions/2026-09-03_gate2-lane2-deferred-security-note.md`).
> Reviewed by: cx.deepthink, 2026-09-05. Dispatched early on the user's
> explicit direction rather than waiting for the originally-scheduled
> 2026-09-07 date — the quota-exhaustion assumption behind that date was
> stale; cx has been available and reliable throughout the session.
>
> Reviewed: `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md` (526 lines, SHA-256
> `d6096fcf2c9e21e05ee479cdd56f9dfe13a5ec8c4c460aa907f1d97c6295afab`),
> the preliminary `PHASE1-MANIFEST-SCHEMA-V2-PRELIM-SECURITY-REVIEW-2026-09-04.md`
> (195 lines, SHA-256 `69b207c9d253624217c272e37ebad5bc6c55386be704c5b4cb61f38ef0ce3fa0`),
> against repository `main` at `160b1f196f069b693cf84f97e43aca7a1f3ec92d`.
> No files were changed by this review.
>
> **Independently spot-verified by the terminal before this doc was
> committed** (3 of the most load-bearing, surprising claims, checked
> directly against real source rather than trusted on report alone):
> `adapter_id`/`peer_kind`'s schema patterns are genuinely ASCII-only
> (`^[a-z0-9-]+$` / `^[a-z]+$`, confirmed by direct grep) — correcting the
> preliminary review's Unicode-homoglyph finding for those two fields
> specifically, while confirming the risk still applies to `aliases`/
> `profile_id`/`shim_names`, which have no pattern constraint at all; the
> claimed JSON-Schema trailing-LF bypass is real, reproduced directly with
> this environment's actual `jsonschema==4.26.0`
> (`Draft202012Validator({"type":"string","pattern":"^[a-z0-9-]+$"}).is_valid("evil\n")`
> returns `True`); the claimed `env=None`-means-full-ambient-inheritance
> regression is real, confirmed in `peerhub/application/workflows.py`
> (line 896: `env=dict(invocation_plan.environment_delta) if
> invocation_plan.environment_delta else None`) and `peerhub/dispatch/pipe.py`'s
> own `PipeRunnerConfig.env` docstring ("defaults to inheriting the parent
> environment").

---

## Independent verdict

**DO NOT SHIP Lane 2 as a runnable adapter path under the Phase 1 model.**

Phase 2 pre-spawn revalidation is a hard prerequisite for repeated execution, but **Phase 2 as currently described is still insufficient**. Revalidation proves only that bytes have not changed since admission; it does not establish that the manifest, executable, or requested invocation was trustworthy at admission.

The only safe Phase 1 subset is passive discovery into a separate, non-runnable candidate inventory. Discovery must not register a normal `PeerAdapter`, run `--version`, perform a readiness probe, inherit secrets, or make the candidate selectable for dispatch.

## The preliminary review missed the shortest code-execution path

Its findings 1–3 are real, but an attacker does not need TOCTOU, junction replacement, or PATH manipulation.

A manifest may declare itself `"active"`, choose an arbitrary executable target, and supply arbitrary `argv`, `cwd`, `stdin`, and environment policy. Phase 1 then hashes whatever target is present and treats that hash as a baseline, while explicitly admitting that there is no pre-existing trusted digest or signature (design line 281).

Two immediate attacks follow:

1. The attacker places malicious bytes at the target before admission. Phase 1 faithfully hashes and admits those malicious bytes. No swap is needed.
2. The attacker targets an already-trusted interpreter or system utility and places the payload in `argv` or `stdin`. Hashing or rehashing `powershell.exe`, `cmd.exe`, `python.exe`, `node.exe`, or another interpreter does not authenticate the command being asked of it.

The start-template guard merely requires a prompt placeholder somewhere in `argv` or `stdin` (design lines 283–286); it does not constrain the rest of the invocation. The target and invocation fields are broadly attacker-controlled (design lines 93–132, 251–274).

Therefore:

> Admission-time hashing without authenticated provenance or explicit activation is change detection, not trust establishment.

This also invalidates the reasoning elsewhere that bounded built-in decoder engines make the mechanism safe despite arbitrary downstream executables. The child executable is Turing-complete code even if its decoder is first-party (deferral document line 8).

## Independent verification of the ten preliminary findings

| # | Reference verification | Independent judgment | Design acknowledgment |
|---|---|---|---|
| 1 | Accurate. Lines 281 and 302–317 expressly defer pre-spawn revalidation. | **Blocker/Critical.** The window is unbounded. The preliminary review is correct that the hash is forensic rather than preventive. It understates the larger admission-authentication failure above. | Explicitly acknowledged. |
| 2 | Accurate. Lines 299–300 contain the admission-time reparse check; lines 305–309 hash one node. | **High**, but it is a concrete subtype of finding 1 rather than an independent root cause. It applies where the attacker can replace a target path component. | The general TOCTOU is acknowledged; post-admission junction replacement is not specifically handled. |
| 3 | Substantively correct, but the worked-example citation is incomplete: lines 347–402 cover only Claude. Codex and Agy use `path` at lines 423–424 and 480–481. | **High if spawn re-resolves; otherwise mitigated.** The design does not state that spawn must use only the receipt's absolute path. The current runner can re-search PATH and rewrite recognized `.cmd` targets (`peerhub/dispatch/pipe.py` lines 297–336, confirmed). | Not addressed. |
| 4 | References are accurate, but the principal examples are wrong. `adapter_id` and `peer_kind` have ASCII-only patterns (independently confirmed), so Cyrillic lookalikes in those fields do not pass. | **Low–Medium as written; Medium for other identifiers.** Homoglyph attacks remain possible in unconstrained `aliases`, `profile_id`, and `shim_names`. The schema also accepts a final LF in the supposedly ASCII-anchored IDs under the repository's `jsonschema` implementation (independently reproduced). | Not addressed. |
| 5 | Accurate reference, but the preliminary assessment contains an important error. | **High where effective `Authenticated Users:(M)` applies.** `Authenticated Users` includes other authenticated local/domain users, not merely processes owned by the current user. The rule therefore does not reliably prevent cross-account modification. It also ignores explicit write grants to arbitrary other SIDs/groups. | Permission is explicitly allowed; its security consequence is not honestly described. |
| 6 | Accurate. `env_policy.set` permits arbitrary string keys and values. | **High**, and potentially part of a code-execution chain through runtime injection, DLL/module search, child-process PATH lookup, or interpreter configuration. | Not addressed. |
| 7 | Accurate, with one correction: `"*"` is just a literal name unless an implementation invents wildcard semantics. | **High confidentiality impact in composition.** A manifest can request known token/key names individually. Furthermore, the current execution path passes `env=None` when the computed delta is empty, which means full ambient inheritance (independently confirmed: `workflows.py` lines 893–899, `pipe.py` lines 107–117). Lane 2 must not inherit that behavior. | Not addressed. |
| 8 | Accurate and correctly conditional on the regex implementation. | **Medium DoS.** An attacker controls both the pattern and, through the executable, the matched output, making catastrophic behavior easy to trigger if Python `re` is used. | Not addressed. |
| 9 | Accurate. The collision section includes aliases, but does not explicitly include built-in registrations or precedence. | **Medium; potentially High if integration permits replacement.** The existing registry initializes built-ins first and rejects duplicate kinds/aliases (`peerhub/adapters/registry.py` lines 46–79, 106–125), which is a useful mitigation. The proposed RCU registry must preserve that invariant explicitly. | Partially addressed by manifest-to-manifest collision rules, not built-in precedence. |
| 10 | Accurate. Line 339 explicitly specifies whole-snapshot rejection. | **Medium availability/stale-policy risk.** A bad file blocks candidate publication. If the old snapshot remains active, the more serious consequence is that an attacker may freeze an old executable or prevent a security revocation/update from publishing. | The behavior is explicit; the abuse and stale-trust consequence are not discussed. |

## Additional security findings

### 1. Untrusted `"status": "active"` is treated as activation authority — Blocker

There is no authenticated publisher, signature, configured trust root, administrator action, or unavoidable user approval in this document. A file writer can self-activate. An `"active"` field in an untrusted inbox must be treated as a request for activation, never authorization.

### 2. The manifest is effectively executable policy — Blocker

Even a perfectly authenticated and immutable executable can be abused through arbitrary arguments, stdin, working directory, environment variables, wrapper/interpreter selection, and child-process/module search paths. The complete canonical manifest — not merely the executable hash — must be approved and bound into the trust receipt. Any change to templates, environment, capabilities, engine, profiles, or executable invalidates approval.

### 3. The "native binary" validation rule is not representable by the schema — High

Section 4.3 says "If the claim is `NATIVE_BINARY`," perform an MZ check (design line 308). No executable-type or node-role claim exists in the schema. Even if it did, an MZ prefix is not authentication, publisher verification, or complete PE validation.

### 4. Wrapper and dependency binding remains unsafe beyond the admitted node — Blocker

Hashing `claude.cmd` or `codex.cmd` does not bind the executable or script it launches. The current runner specifically bypasses those wrappers and discovers sibling binaries or a PATH-resolved `node.exe` at runtime (`pipe.py` lines 297–336, `peerhub/core/binary_resolution.py` lines 17–64). Static tracing alone cannot establish a complete runtime execution closure. The security boundary must include publisher/user authorization, environment/search-path hardening, and actual process confinement — not merely more hashes.

### 5. ACL evaluation is insufficient — High

The policy checks only a few broad principals while permitting `Authenticated Users:(M)`. It does not require that every write-capable ACE resolve exclusively to the active principal, Administrators, or SYSTEM. Also missing: ownership/DACL checks for every directory component and the executable itself; manifest-directory and manifest-file ACLs; hard links (`st_nlink > 1`, not reparse points); alternate paths to the same file identity; effective-access evaluation for arbitrary groups/SIDs; race-free evaluation from the same handle used for hashing.

### 6. Manifest reading is unspecified and raceable — High

No specified: max file size/count/total bytes/nesting depth/string/array length; strict UTF-8 policy; duplicate-key rejection; invalid/lone-surrogate/control-character handling; no-follow opening of scan root and manifests; rejection of manifest reparse points/hard links; stable file identity while reading; read/hash/parse from the same immutable byte buffer; deterministic behavior under concurrent filesystem mutation. A pathname-based stat→ACL→open→parse→hash sequence can be raced inside admission itself.

### 7. Resolution rules lack safe, discriminated semantics — High

`target` is an unrestricted string for all three rules (`absolute`/`sibling`/`path`). Each needs its own validated type (full DOS path only, no UNC/device-namespace/ADS; single leaf name only, no separators/`..`/drive prefixes; single executable leaf name with a frozen approved search list, not ambient PATH). The schema currently allows traversal or an absolute pathname inside a `path`/`sibling` target.

### 8. The JSON Schema has real validation gaps — High/Medium

Reproduced directly with `jsonschema==4.26.0`: the schema passes `Draft202012Validator.check_schema`, all 3 worked examples validate, and the `engine_id`/`options` if/then binding works — but it also accepts `adapter_id: "evil\n"` (trailing-LF bypass, reproduced), empty `profiles`/`supported_transports` arrays, duplicate `profile_id` objects, empty `argv` when the placeholder is in `stdin`, empty/traversal-like/confusable aliases, arbitrary dangerous environment variable names, a PTY profile paired with a JSON engine, and unbounded integers/strings/regexes/arrays/profiles/templates. `canonical_json(M_i)` is not defined precisely enough for a security digest (no named canonicalization standard, Unicode treatment, duplicate-key rule, integer rendering, byte encoding). Duplicate JSON object keys are unspecified — normal Python JSON parsing silently reduces `{"status":"inactive","status":"active"}` to `"active"`, dangerous for both human review and canonical-digest agreement.

### 9. Publication is not yet a safe RCU protocol — High/Medium

Missing: a single serialized writer or CAS publication; generation allocation inside the same critical section; deep immutability of every nested mapping/list/factory/engine-option/receipt; a coherent candidate scan rather than a mixture of files from different moments; explicit failure/retry semantics; overflow handling; integration that doesn't mutate the current live registry dictionaries. `register_adapter_factory()` (`registry.py` lines 84–125) is explicitly lock-free by its own comment, safe only under serialized import/test setup — unsuitable for concurrent hot reload. More importantly: reader pinning creates a **revocation gap** — a request that pins an old registry may still spawn after the manifest or adapter is removed/quarantined. Pinned invocation config must be checked against a current revocation/trust epoch immediately before spawn, separately from the pinned snapshot itself.

### 10. Third-party adapters become indistinguishable from first-party adapters — High

`ResolvedPeerTarget` (`registry.py` lines 31–38) carries no trust class, publisher, manifest digest, admission receipt, or registry generation — only name, peer kind, adapter, profile, executable path. Downstream code compares the adapter's self-described `peer_kind` during pre-spawn capability checks, trusts self-described capabilities for SESSION/STREAM behavior, labels resolver-provided base nodes `registered_by: "system"` (`peer_registry.py` lines 149–173), and eventually spawns normally (`workflows.py` lines 639–664, 828–916). A third-party target needs an explicit trust tier so declared capabilities/readiness/proof kinds/provider IDs are never mistaken for measured facts. `readiness_probe_id`/`usage_provider_id` need closed allowlists — a readiness probe is itself code execution (`bootstrap.py` lines 121–139 launch the target for `--version`). Passive discovery must never call this before activation.

### 11. Inactive manifests and collision rules create additional DoS ambiguity — Medium

Undefined: whether `"inactive"` manifests undergo executable validation, reserve identity claims, participate in collisions, or can block the entire candidate snapshot. `normalize_key`'s extension-stripping is applied generically to all claim types even though executable-extension semantics should apply only to executable/shim names — creates unnecessary collisions and cheap DoS potential.

## Required implementation checklist

### P0 — Must exist before any third-party-declared process or readiness probe can run

- [ ] Split the directory into an untrusted candidate inbox and a protected, authoritative activation registry. Files in `adapters.d` are data only.
- [ ] Require either a verified publisher signature/package trust chain or an explicit, unavoidable interactive user/admin activation.
- [ ] Ignore manifest `"status": "active"` unless a separate protected trust grant authorizes the exact manifest.
- [ ] Bind the trust grant to the canonical manifest digest, complete invocation templates, environment policy, engine implementation/version, adapter identity, capabilities, profiles, final executable path, file identity, content hash, publisher/signature evidence, and registry generation.
- [ ] Invalidate activation on any change to those facts. Require reapproval unless a configured publisher policy authorizes the update.
- [ ] Never allow `--yes`/headless activation of an unsigned manifest. Noninteractive operation must fail closed unless a preconfigured trust root verifies it.
- [ ] Never run discovery-time `--version`, readiness, usage, health, or conformance probes before activation.
- [ ] Reserve all built-in peer kinds, adapter IDs, profiles, aliases, shim names, probe IDs, and provider IDs. Third-party identities should use an explicit namespace and retain `source=third-party` throughout routing, health, audit, and dispatch.
- [ ] Treat declared capabilities and proof requirements only as claims. Machine-owned evidence and policy must determine actual authorization.

### P0 — Pre-spawn integrity and race closure

- [ ] Use only the receipt-bound absolute target. Never re-resolve through PATH at spawn.
- [ ] Revalidate the current trust/revocation generation immediately before every spawn.
- [ ] Re-open the target and every admitted launch-chain node with no-follow semantics.
- [ ] Verify local NTFS volume, final path, volume identity, file ID, owner, effective DACL, link count, reparse status, size, and hash against the receipt.
- [ ] Hold handles/locks that deny write/delete/rename through process creation, including relevant path components, or execute from a protected content-addressed copy.
- [ ] For a strong Windows implementation, restrict the first release to directly launched native images and use a Windows-specific launcher capable of suspended creation and image-identity verification before resume.
- [ ] Do not describe "rehash immediately, close the handle, then call `Popen`" as closing TOCTOU. It only narrows the race.
- [ ] Reject wrappers/scripts/interpreters in the initial unattended-spawn release unless the full launch chain and invocation policy have a separately ratified security design.
- [ ] Do not use the current `_resolve_real_direct_binary()` fallback for receipt-bound third-party targets.

### P0 — Environment and invocation policy

- [ ] Build an explicit minimal environment and always pass it as a mapping; never use `env=None` as the empty-policy behavior.
- [ ] Treat Windows environment keys case-insensitively and reject duplicates such as `PATH`/`Path`.
- [ ] Deny runtime-injection variables by default, including language startup/module variables, DLL search controls, `COMSPEC`, `PATHEXT`, and arbitrary PATH replacement.
- [ ] Require a separate user permission for each inherited secret-bearing variable. Default to no secret inheritance.
- [ ] Define precedence among mandatory system values, inherited values, and manifest-set values.
- [ ] Use a closed placeholder grammar with whole-token substitution by default.
- [ ] Prohibit placeholders in executable identity and prevent substring tricks that obscure audit display.
- [ ] Prefer prompt delivery through stdin or bounded artifacts; do not pass untrusted content through shell wrappers.
- [ ] Restrict `cwd` to a canonical authorized workspace root and define project-config/hook risks.

### P1 — Parser, schema, filesystem scanning

- [ ] Strict UTF-8 byte decoding with explicit BOM and Unicode-scalar policy.
- [ ] Reject duplicate JSON object keys.
- [ ] Set per-file, file-count, total-byte, depth, array, string, regex, and integer limits.
- [ ] Replace vulnerable `$` identifier anchors with explicit semantic full-match validation.
- [ ] Add safe ASCII patterns and length limits for all identifiers, aliases, profiles, probes, providers, artifact IDs, and shim names.
- [ ] Add `minItems`, `uniqueItems`, and semantic uniqueness rules.
- [ ] Require at least one profile, at least one supported transport, and non-empty invocation argv.
- [ ] Enforce profile transport ⊆ supported transports and valid engine/transport pairings.
- [ ] Define an exact canonical JSON algorithm.
- [ ] Define `absolute`, `sibling`, and `path` targets as distinct validated types.
- [ ] Open the scan root and manifest entries without following reparse points; reject hardlinks and non-regular files.
- [ ] Read, hash, and parse once from the same bounded byte buffer and file identity.

### P1 — Registry publication and revocation

- [ ] Serialize writers or publish with CAS over the observed generation.
- [ ] Deep-freeze snapshots and factories; do not expose live mutable dictionaries.
- [ ] Define when a request becomes "in flight."
- [ ] Separate pinned invocation configuration from a current revocation epoch checked at spawn.
- [ ] On executable or manifest revalidation failure, immediately disable that adapter even if unrelated candidate files are malformed.
- [ ] Quarantine invalid individual files and publish unaffected adapters.
- [ ] For collisions, preserve built-ins, quarantine all conflicting third-party claims, and never select a winner by enumeration order.
- [ ] Ensure a malformed added file cannot freeze revocation of an already-active adapter.

### P1 — Adversarial tests required before ratification

- [ ] Malicious executable present before admission.
- [ ] Trusted system interpreter plus malicious argv/stdin.
- [ ] Executable replacement, directory junction swap, symlink swap, hardlink mutation, and in-place write during hash.
- [ ] PATH/PATHEXT mutation and relative PATH components.
- [ ] Manifest-file reparse and mutation during scan.
- [ ] Duplicate JSON keys, final-LF identifiers, huge/deep inputs, and Unicode/confusable identifiers.
- [ ] Dangerous environment variables, case-colliding variables, and empty-policy full-inheritance regression.
- [ ] Built-in identity/alias/profile/probe/provider spoofing.
- [ ] Concurrent reloaders publishing from the same generation.
- [ ] Removal or quarantine while a request holds an old snapshot.
- [ ] Malformed/inactive manifest attempting to block a security revocation.
- [ ] Readiness-probe proof that zero processes are spawned before activation.

## Concrete middle ground (recommended, not yet authorized for implementation)

Lane 2 may ship in a deliberately inert form:

1. Scan the directory with the hardened reader.
2. Produce `CandidateAdapter` records only.
3. Display them separately as "untrusted/unactivated."
4. Do not insert them into `resolve_peer_target()`, the health membership set, routing candidates, alias resolution, or the runnable adapter registry.
5. Do not execute readiness or version probes.
6. When a user explicitly chooses "Activate," show the publisher/signature state, absolute executable path, hashes, full argv templates, cwd policy, inherited/set environment names, requested secrets, capabilities, profiles, and engine.
7. Store approval outside `adapters.d` in protected authoritative state, bound to the complete digest set.
8. Perform the atomic pre-spawn validation above.
9. Re-prompt or fail closed on any change.

That is a real implementation boundary. Passive candidate discovery can proceed independently; executable trust cannot.

**Final vote: REJECT / DO NOT RATIFY runnable Lane 2 under Phase 1. Ratify only the inert candidate-discovery subset. Require an amended Phase 2 containing both authenticated activation and atomic pre-spawn identity enforcement before any third-party-declared process is spawned.**
