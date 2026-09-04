# Preliminary Adversarial Security Review
## PHASE1-MANIFEST-SCHEMA-V2-2026-08-20 — Adapter-Manifest Admission Design

> [!NOTE]
> **Status**: Preliminary / second-opinion review per the deferred-security-note. This does NOT substitute for the ratified cx peer review scheduled for 2026-09-07. Reviewed by: ag.opus, 2026-09-04.

> [!IMPORTANT]
> **Threat model assumed**: Local attacker with unprivileged write access to `%LOCALAPPDATA%\PeerHub\adapters.d` (standard user, no admin, no SYSTEM). Goal: get an attacker-controlled executable admitted as a trusted AI peer and eventually spawned by PeerHub.

---

## Finding 1 — TOCTOU: Explicit, Acknowledged, Wide-Open in Phase 1

**Design-doc reference**: [§4 preamble](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L279-L281), [§4.3](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L302-L317)

**The gap**: The design doc *explicitly acknowledges* that Phase 1 has no pre-spawn revalidation. The admission pipeline hashes the executable at admission time and records the hash in the `AdmissionReceipt`, but there is no described step that re-checks the hash before `subprocess.Popen`. This means an attacker who can write to the executable's location (or who can swap the target via a junction — see Finding 2) has an unbounded TOCTOU window: admission pins the hash, then any time between admission and eventual spawn, the file can be replaced with attacker-controlled bytes.

**Severity**: This is the single most exploitable gap. The design doc does document it as a known Phase 1 limitation ("closing that gap is part of Phase 2 pre-spawn revalidation"), but it is worth underscoring: **in Phase 1, the hash-pinning is forensic-only, not preventive**. An attacker doesn't need to beat a race — they have arbitrary time.

**Concrete attack**: Drop a legitimate manifest pointing at a real `claude.cmd`. Wait for admission. Replace `claude.cmd`'s contents (or the `.exe` it resolves to) at leisure. Next time PeerHub spawns it, the attacker's payload executes with the user's full token.

**Assessment**: The design is honest about this. No *undocumented* gap here, but it should be called out as a **ship-blocker for Phase 1 if third-party adapter manifests are actually admitted in Phase 1** (which the deferred-security-note's Lane 2 deferral already prevents). If Phase 1 is truly limited to Lane 1 built-in adapters only, the practical risk is contained.

---

## Finding 2 — Symlink/Junction Swap After Hashing Defeats Single-Node Pinning

**Design-doc reference**: [§4.2 item 4 (Reparse Point / Junction Safety)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L299-L300), [§4.3 (Single-Node Hashing)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L302-L309)

**The gap**: §4.2 item 4 says "No directory component in the path may traverse an unverified symlink, volume mount point, or junction point." This check is described as happening **at admission time**. But there is no described mechanism to verify that the path has not been modified to include a junction/symlink *after admission but before spawn*. Combined with Finding 1 (no pre-spawn revalidation), the attack is:

1. At admission time, `C:\Users\victim\AppData\Local\PeerHub\adapters.d\evil\legit.exe` is a real file with legitimate bytes. No junctions in path. Admission passes. Hash pinned.
2. After admission, attacker replaces the `evil` directory (or a parent) with an NTFS junction pointing to `C:\Users\victim\attacker-controlled\`, where a different `legit.exe` lives.
3. At spawn time, PeerHub resolves the same path string, traverses the new junction, and executes the swapped binary. The pinned hash is never rechecked.

On standard Windows, creating a directory junction within your own `%LOCALAPPDATA%` requires no elevation — `mklink /J` works for any user within their own profile directories.

**Severity**: High. This is a concrete bypass of both the junction-safety check and the hash-pinning, due to the temporal gap between admission and spawn.

**Assessment**: The design's junction-safety check is sound *at the point-in-time it runs*, but the lack of pre-spawn revalidation makes it a one-shot gate that an attacker can circumvent after it passes.

---

## Finding 3 — `resolution_rule: "path"` Introduces an Entirely Separate TOCTOU via PATH Manipulation

**Design-doc reference**: [§4.3 item 1 (Target Resolution)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L306), [§3 schema: `execution.executable`](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L98-L105), [§6 examples](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L347-L402)

**The gap**: The schema allows `resolution_rule: "path"`, meaning the `target` (e.g., `"claude.cmd"`) is resolved via the system PATH at admission time. All three worked examples use `"path"` resolution. The design says the resolved path is hashed and pinned — but the *resolution itself* is PATH-dependent, and `%PATH%` is mutable per-process and per-user.

Attack scenario:
1. At admission time, `claude.cmd` resolves via PATH to `C:\legit\claude.cmd`. Hash pinned.
2. Attacker modifies the user-scoped `PATH` environment variable (no admin required — `setx PATH ...` or registry edit under `HKCU\Environment`) to prepend a directory containing attacker's `claude.cmd`.
3. If the spawn-time code re-resolves the `target` via PATH (to construct the argv), it now resolves to the attacker's binary. The pinned hash is for the old path.

**Key question the design doesn't answer**: Does spawn-time use the *pinned absolute path* from the admission receipt, or does it re-resolve the `target` string from the manifest via PATH? If the former, this finding is mitigated (but the absolute path is still subject to Finding 2). If the latter, PATH hijacking is a trivially exploitable gap.

**Assessment**: The design should explicitly specify that **only the canonical absolute path from the admission receipt is used at spawn time**, never re-resolving from PATH. This is not currently stated.

---

## Finding 4 — Unicode Normalization Collision Attack on Human Reviewers

**Design-doc reference**: [§5.1 (`normalize_key`)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L324-L328), [§5.2 (Collision Rules)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L330-L334)

**The gap (partially)**: The normalization algorithm (NFC → casefold → strip trailing whitespace/dots → strip extensions) is designed to *prevent* two manifests with confusable names from coexisting. This is good — it means an attacker *cannot* register both `claude-peer` and `сlaude-peer` (Cyrillic `с`) because NFC + casefold would need to collapse them.

**However**: NFC normalization does **not** collapse homoglyphs across scripts. `casefold()` is Unicode-aware but does not perform confusable-detection (TR39/UTS39). Specifically:
- `claude-peer` (Latin) vs `сlaude-peer` (leading Cyrillic es U+0441) — these are **different** after NFC + casefold. Both would be admitted as distinct `adapter_id` values. The collision algorithm would NOT flag them.
- Similarly, Cyrillic `а` (U+0430) vs Latin `a` (U+0061), Greek omicron `ο` (U+03BF) vs Latin `o` (U+006F), etc.

An attacker could drop a manifest with a visually-identical `adapter_id` or `peer_kind` that passes collision detection, because the normalized keys genuinely differ at the codepoint level.

**Practical severity**: Medium. This depends on whether any human review step is in the admission pipeline. If admission is fully automated (drop file → auto-admitted), a confusable name tricks no human — but it could still confuse log reviewers or the operator looking at the registry after the fact. If there's any manual approval step, this is directly exploitable for impersonation.

**Recommendation**: The normalization algorithm should include a **mixed-script restriction** (reject identifiers containing codepoints from multiple Unicode scripts, per UTS39 §5.2) or a confusable-skeleton check.

---

## Finding 5 — ACL Checks Don't Cover User-Scoped Modification by Other Processes Running as the Same User

**Design-doc reference**: [§4.2 (Windows ACL Evaluation)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L288-L300)

**The gap**: The ACL evaluation denies `Everyone`, `ANONYMOUS LOGON`, and `BUILTIN\Guests`, and allows `Authenticated Users:(M)`. It checks ownership against `Administrators`, `SYSTEM`, or the active user SID.

This means: **any process running as the current user can modify the executable after admission**. The ACL check explicitly *permits* `Authenticated Users:(M)` (§4.2 item 3), which includes the current user and any process running under that user's token. This is a design-level acknowledgment that the ACL gate does not protect against same-user-context attacks.

This is rational (you can't meaningfully ACL-fence a user from themselves on a single-user workstation), but it means the real security boundary is:
- The ACL check prevents **other unprivileged users on the same machine** and **anonymous/guest** access from planting or modifying executables.
- It does **not** prevent malware already running as the current user from modifying admitted executables.

**Assessment**: This is a **design-level limitation that is realistic for the threat model** (local workstation, single active user), but it should be explicitly documented as an accepted residual risk. The design currently implies the ACL check is a complete gate without calling out this boundary.

---

## Finding 6 — `env_policy.set` Allows Manifest-Controlled Environment Variables Injected into the Spawned Process

**Design-doc reference**: [§3 schema: `env_policy`](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L120-L132), [§6 examples](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L347-L517)

**The gap**: The `env_policy.set` field allows a manifest to define arbitrary environment variables that will be set in the spawned process's environment. There is no validation, allowlist, or restriction on which variable names or values can be specified.

A malicious manifest could set:
- `PATH` — prepend attacker-controlled directories to override binary resolution within the spawned process's own subprocess calls.
- `LD_PRELOAD` / `DYLD_INSERT_LIBRARIES` — irrelevant on Windows, but shows the class of risk.
- On Windows specifically: `COMSPEC`, `PATHEXT`, `SYSTEMROOT`, `APPDATA`, or other process-affecting variables that could redirect behavior of the spawned executable or its children.
- `NODE_OPTIONS`, `PYTHONPATH`, `PYTHONSTARTUP`, `PERL5OPT`, `RUBYOPT` — language-runtime injection vectors if the spawned executable is an interpreter-based tool.

The worked examples in §6 all use `"set": {}` (empty), which is benign. But the schema permits arbitrary key-value pairs with no restriction.

**Severity**: Medium-High. If a malicious manifest is admitted (which is the threat model), `env_policy.set` provides a clean vector for modifying the behavior of even a legitimate, hash-verified executable by controlling its runtime environment.

**Recommendation**: Either (a) allowlist permitted environment variable names, or (b) document that `env_policy.set` is treated as part of the "trusted manifest content" and therefore only safe insofar as the manifest itself is trusted.

---

## Finding 7 — `env_policy.inherit` Lacks Restriction and Could Leak Sensitive Environment Variables

**Design-doc reference**: [§3 schema: `env_policy.inherit`](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L124-L125)

**The gap**: `env_policy.inherit` is an array of environment variable names to pass through from the PeerHub process to the spawned adapter. There is no blocklist or sensitivity check. A malicious manifest could request inheritance of sensitive variables that the user has set in their environment (API keys, tokens, secrets), causing them to be exposed to the spawned process.

If the spawned process is itself attacker-influenced (per Findings 1–3), this becomes an exfiltration path: spawn attacker's binary with the user's API keys in its environment.

Even if the spawned process is legitimate, `inherit` has no documented upper bound — a manifest could request `*` or a very large set of variables, exposing the adapter to information it should not need.

**Severity**: Low-Medium in isolation (it inherits what the parent PeerHub process already has), but compounds with the TOCTOU findings to enable credential theft.

**Assessment**: The design should specify whether `inherit` is treated as a trusted declaration or whether a blocklist of known-sensitive variable names (e.g., `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`) is enforced.

---

## Finding 8 — `pty-legacy-v1` Engine's `success_regex` / `error_regex` Are Attacker-Supplied Regular Expressions

**Design-doc reference**: [§3 schema: `engine.options` for `pty-legacy-v1`](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L204-L222)

**The gap**: For `engine_id: "builtin:pty-legacy-v1"`, the manifest supplies `success_regex` and optionally `error_regex` as strings. These are presumably compiled and executed as regular expressions by the admission engine or the runtime.

If these strings are passed directly to Python's `re.compile()`, a malicious manifest can supply a [ReDoS](https://owasp.org/www-community/attacks/Regular_expression_Denial_of_Service_-_ReDoS) payload — a catastrophically backtracking regex pattern that consumes CPU and effectively DoS's the PeerHub process.

Example: `"success_regex": "^(a+)+$"` matched against a long string of `a`s followed by a non-`a` character causes exponential backtracking.

**Severity**: Medium (denial-of-service against PeerHub, not code execution).

**Recommendation**: Use `re2` or enforce a regex complexity budget, or validate the regex pattern at admission time against known pathological patterns.

---

## Finding 9 — `aliases` Field Has No Schema Constraint Against Overriding Built-in Peer Kinds

**Design-doc reference**: [§3 schema: `adapter.aliases`](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L64-L66)

**The gap**: The `aliases` array is typed as `{ "type": "array", "items": { "type": "string" } }` with no pattern constraint or reserved-name check. A malicious manifest could declare `"aliases": ["cc", "cx", "ag"]`, attempting to claim the built-in peer kinds as aliases.

Whether this succeeds depends on whether the collision algorithm in §5.2 treats `aliases` as part of the claim space — the text says "Extract normalized `adapter_id`, `peer_kind`, `profile_ids`, `shim_names`, and aliases" and checks for collisions, so this *should* be caught if built-in adapters are already registered.

**Residual risk**: If the malicious manifest is processed *before* the built-in adapters (e.g., filesystem enumeration order), it could claim the alias first, and the built-in would then be rejected as the "collider." The design doesn't specify processing order or priority.

**Assessment**: The collision algorithm likely handles this, but the design should explicitly specify that built-in adapters have priority and are never rejected in favor of a third-party manifest claiming the same names.

---

## Finding 10 — Atomic Snapshot Rejection Is All-or-Nothing, Enabling a Griefing/DoS Vector

**Design-doc reference**: [§5.3 item 3 (Atomic Rejection)](file:///P:/workspace/peerhub/docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md#L339)

**The gap**: "If ANY manifest fails JSON schema, ACL checks, semantic template checks, single-node executable validation, or triggers a collision, the **entire candidate snapshot is rejected**."

An attacker with write access to `adapters.d` can **permanently prevent any third-party adapter from being admitted** by continuously dropping a single malformed manifest file. Every scan cycle would encounter the bad manifest, fail it, and reject the entire snapshot — including all legitimate manifests.

This is a trivial denial-of-service: one bad `.json` file in the directory blocks all adapter discovery indefinitely.

**Severity**: Medium (availability, not integrity).

**Recommendation**: The design should distinguish between "reject the individual bad manifest and continue" vs. "reject the entire snapshot." The all-or-nothing model makes sense for collision detection (where the *relationship* between manifests matters), but individual validation failures (schema, ACL, template guards) could be handled by excluding the failing manifest and proceeding with the remainder.

---

## Summary

| # | Finding | Severity | Design-Doc Acknowledged? |
|---|---------|----------|--------------------------|
| 1 | TOCTOU: no pre-spawn hash revalidation in Phase 1 | **Critical** | Yes (explicitly deferred to Phase 2) |
| 2 | Junction/symlink swap after admission bypasses both junction-safety and hash pinning | **High** | Partially (junction check is admission-time only, not re-checked) |
| 3 | `resolution_rule: "path"` — unclear if spawn uses pinned path or re-resolves via PATH | **High** | Not addressed |
| 4 | Unicode homoglyph smuggling past collision detection (NFC+casefold ≠ confusable detection) | **Medium** | Not addressed |
| 5 | ACL checks don't protect against same-user-context modification (by design, but undocumented) | **Low-Medium** | Implicit, not explicitly documented |
| 6 | `env_policy.set` allows arbitrary environment variable injection | **Medium-High** | Not addressed |
| 7 | `env_policy.inherit` has no sensitive-variable blocklist | **Low-Medium** | Not addressed |
| 8 | `pty-legacy-v1` regex fields are a ReDoS vector | **Medium** | Not addressed |
| 9 | `aliases` could claim built-in names if processing order isn't defined | **Low** | Partially (collision detection exists but priority undefined) |
| 10 | All-or-nothing snapshot rejection enables trivial DoS via one bad manifest | **Medium** | Not addressed |

> [!CAUTION]
> **Findings 1–3 compose into a single kill chain**: Drop a legitimate-looking manifest (Finding 9 for naming), get it admitted, then swap the executable via junction (Finding 2) or PATH manipulation (Finding 3) during the unbounded TOCTOU window (Finding 1), with attacker-controlled environment variables (Finding 6) for good measure. This chain achieves arbitrary code execution under the user's token with the user's API keys, purely from unprivileged local write access to `adapters.d`.
>
> The design doc is honest about the Phase 1 TOCTOU limitation, and the deferred-security-note's decision to defer Lane 2 (third-party manifests) is the correct mitigation until Phase 2 pre-spawn revalidation is implemented. **Do not ship Lane 2 third-party admission with Phase 1's validation model.**
