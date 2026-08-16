# EvidenceArtifact / 3-Tier Context Partitioning Design

**Date:** 2026-08-16
**Status:** RATIFIED (round 1 ag.deepthink draft; round 2 cc.effort
independent critique found the core mechanism depended on peerhub
capabilities that don't exist anywhere -- tool-call interception/
registration -- and was NOT READY; round 3 ag.deepthink rebuilt around
accord clause 11's actual one-way caller-side offload instead of a live
bidirectional protocol, eliminating every capability the mechanism
previously required; terminal independently re-verified the write-path
reuse claim and the "known path" artifact-retrieval claim against real
source -- `ArtifactMetadata.staging_ref` genuinely exists -- before
ratifying). Implementation may proceed directly; unlike the health/quota
design, this one has no canary precondition -- every dependency is
already-working peerhub code.
**Target:** `peerhub/`

This document details the concrete implementation mechanics for the `EvidenceArtifact` / 3-Tier Context Partitioning feature (item P2 in the multi-ai-collaboration-accord). It defines how oversized tool and MCP outputs are intercepted, kept out of the prompt, and stored on disk.

## 1. Concrete Type System Definition
`EvidenceArtifact` is a new dataclass that will live in `peerhub.dispatch.contract`. It serves as the canonical record of an offloaded piece of evidence.

**Fields:**
- `artifact_id: str` - A stable, typed handle (e.g., `evidence://ev_1234abcd`).
- `source_tool_name: str` - Provenance indicating which tool or MCP call generated this payload.
- `content_length: int` - Total size of the artifact in bytes.
- `sha256_hex: str` - Digest for integrity verification.
- `created_at: datetime` - Lifecycle start.
- `expires_at: datetime` - Expiration threshold for garbage collection.

*(Note: While there is no longer a read-back API for the model, all these fields are retained. `content_length` is now used purely for the prompt substitution summary and metadata reporting rather than bounds-checking, and the rest remain essential for integrity and GC.)*

## 2. Interception Call Sites (The Dispatch Flow)
To intercept oversized tool output *instead* of it being inlined, the interception must happen at the adapter boundary during invocation planning, before the prompt is finalized.

**Changes required:**
1. **`peerhub.adapters.contract.AdapterRequest`**: Add a new field `evidence_payloads: tuple[EvidencePayload, ...]`. The upstream caller must provide structured tool outputs rather than pre-inlining everything into a massive `prompt_content` string.
2. **`peerhub.adapters.claude_adapter.RealClaudeAdapter.plan_invocation`** (and `RealCodexAdapter.plan_invocation`): This is the exact interception site. The adapter iterates over `evidence_payloads`. If a payload exceeds the inline threshold (e.g., `PromptPolicy.max_inline_utf8_bytes`), the adapter intercepts it as described in Section 3.

## 3. One-Way Offloading and Prompt Substitution
When `peerhub` is about to construct a dispatch's prompt content and any individual payload (e.g., a tool or MCP result) would exceed the configured size threshold, `peerhub` handles it entirely as a one-way, caller-side decision *before* the prompt is ever sent to the peer. 

**Interception and Substitution Mechanics:**
1. In `peerhub.adapters.claude_adapter.RealClaudeAdapter.plan_invocation` (and `RealCodexAdapter.plan_invocation`), the adapter evaluates the sizes of `evidence_payloads`.
2. If a payload exceeds the threshold (e.g., `PromptPolicy.max_inline_utf8_bytes`), the adapter constructs an `ArtifactSpec` containing the payload bytes and appends it to the returned `InvocationPlan.artifacts`. This routes it to the `ArtifactMaterializer`'s write path.
3. Instead of embedding the full payload into the prompt, the adapter substitutes a concise reference and summary string into the prompt text.

**Substitution String Format:**
The substituted text placed in the prompt will look like this:
`<large output was NNN bytes, offloaded to evidence://ev_xxxx, summary: [First M characters or specific summary...]>`

The peer model never actively issues a request for a slice; it simply sees this substitution string in its prompt and works from the summary. If a human or a later peerhub-side process needs the full content, it reads the materialized file directly from disk via its known path, exactly the same way any other materialized artifact is retrieved in existing dispatch results today. There is no read-back mechanism or API for the model.

## 4. Relationship to the EXISTING ArtifactMaterializer
This design **heavily reuses** the existing `ArtifactMaterializer`.

**Justification:** The `ArtifactMaterializer` already safely handles `ArtifactSpec` payloads, writing them to an isolated `staging_dir`, verifying SHA256 digests, and tracking staging paths. By translating an intercepted `EvidencePayload` into a standard `ArtifactSpec` inside the adapter's `plan_invocation`, `workflows.py`'s existing `dispatch_and_execute` naturally passes it to `ArtifactMaterializer.materialize_manifest`. We gain secure disk storage, staging validation, and lifecycle metadata completely for free.

**Glue Code:** While the disk I/O subsystem requires zero additional logic, a small amount of glue code *is* still needed in the adapter: specifically, the size-threshold check and the string substitution logic itself within `plan_invocation`.

## 5. Failure and Absence Handling
Because this is a one-way, caller-side operation, failures can only occur during the materialization attempt (e.g., disk full, permission denied) before the prompt is finalized.

- **Materialization Failure:** If writing the `ArtifactSpec` fails during dispatch, the invocation must fail closed immediately. The caller receives a dispatch failure (e.g., `ArtifactMaterializationError`), and the prompt is not sent to the model. We do not attempt to silently drop the payload or proceed without it.
*(Note: There are no "out of bounds" or "expired slice" failure modes in this increment, as the model has no API to request reads.)*

## 6. Scope Boundary (What is NOT covered)
This first increment deliberately excludes:
- **Live bounded-slice retrieval by the model mid-conversation:** Explicitly excluded. `peerhub` currently has no tool-call interception or registration mechanism for any of the 3 CLIs today. The CLIs execute sandboxed tool calls and passively report them; there is no capability to intercept a model's slice request before the vendor CLI executes it, nor feed the result back mid-turn. Building this would be a real, separate, larger prerequisite project.
- **Cross-session persistence:** Evidence artifacts share the lifecycle of the attempt/session. They are aggressively garbage collected when the session ends.
- **Cross-peer sharing:** Artifacts remain isolated to the single peer instance's staging directory.
- **Semantic extraction:** JSON-path filtering or semantic chunking is excluded.

## 7. Schema and Migration
This design **does** require a schema migration to durably record `EvidenceArtifact` metadata (tracking provenance, tracking file size for reporting, and expiration lifecycle) within the SQLite database.

Following the `docs/migrations.md` numbering convention, the actual last migration file currently on disk is `0022_retry_authority.sql`. **`0023` is already claimed** by the health/quota tracking design
(`HEALTH-QUOTA-TRACKING-DESIGN-2026-08-16.md`, ratified 2026-08-16,
committed before this design) -- both designs independently reached for
"next free" without checking each other, a real collision caught by the
terminal before either landed as an actual file. Whichever of the two
implements first gets `0023`; this design therefore claims the number
after it:

**`peerhub/persistence/migrations/0024_evidence_artifacts.sql`** (or the
next actually-free number at implementation time if migration state has
moved on by then -- re-check `docs/migrations.md` and the migrations
directory immediately before creating the file, don't trust this
document's number in isolation).

The migration must rigorously adhere to the 12-step fail-closed template, specifically utilizing the in-transaction `PRAGMA foreign_key_check;` before `COMMIT;`.
