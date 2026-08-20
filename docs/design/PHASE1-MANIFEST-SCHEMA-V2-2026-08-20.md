# Phase 1: Manifest Schema v2.0.0 and Admission Model

## 1. Overview
This V2 schema iteration directly addresses Round 5 (item 2) critiques regarding contract mismatch, missing fields, ambiguity in engine vs. manifest responsibilities, and untyped configurations. It brings the declarative manifest into strict 1:1 alignment with `peerhub/adapters/contract.py` and establishes a rigid boundary: the manifest *declares* the shape of the invocation, while the bounded Engine (referenced by `engine_id`) *implements* the Turing-complete protocol behaviors (like stream decoding, completion assessment, and PTY state machines).

## 2. Responsibility Boundary: Manifest vs. Engine

To eliminate the "decoder-only ambiguity" and ensure every `PeerAdapter` behavior has an owner:

**Manifest Responsibilities (Declarative State):**
* Defines exact static `PeerDescriptor` and `ProfileDescriptor` metadata (matching contract field names exactly).
* Defines `PromptPolicy` parameters (`max_inline_utf8_bytes`, `artifact_reference_supported`).
* Defines the template for `InvocationPlan` (argv, cwd, stdin, environment variables, artifacts).
* Selects the bounded Python engine (`engine_id`) and provides explicit, strongly-typed `options` for it.

**Engine Responsibilities (Code/Runtime State):**
* **`plan_invocation()`**: Synthesizes the final `InvocationPlan`. It passes through `limits`, generates `redacted_display` (by stripping secrets/prompts from argv and env), applies `SessionAction` logic to select the `start` vs `resume` template, and maps `evidence_payloads` into the `artifacts` array.
* **`new_decoder()`**: Implements the `OutputDecoder` stream parsing protocol (e.g., SSE, JSONL, PTY scraping) specific to `engine_id`.
* **`interpret_output()`**: Implements `ProtocolAssessment`. The engine is strictly responsible for determining `vendor_completion_marker`, `suspected_truncation`, and `protocol_failure` using its own native code and the explicitly typed `options` provided by the manifest.
* **Graceful Cancel & Interaction**: Handles out-of-band signals or PTY expect/send loops that cannot be expressed in a static payload.

## 3. The JSON Schema Definition (V2)

The `execution.templates` block now directly models the inputs required to build an `InvocationPlan` (cwd, stdin, artifacts), and the fields now match `contract.py` exactly (e.g., `supports_reasoning_effort`, `max_inline_utf8_bytes`).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://peerhub.local/schema/adapter-manifest/v2",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "manifest_version",
    "status",
    "adapter",
    "execution",
    "engine",
    "profiles"
  ],
  "properties": {
    "manifest_version": { "const": "2.0.0" },
    "status": {
      "enum": ["active", "inactive"]
    },
    "adapter": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "adapter_id", 
        "adapter_version", 
        "peer_kind", 
        "capabilities", 
        "transports", 
        "readiness_probe_id"
      ],
      "properties": {
        "adapter_id": { "type": "string", "pattern": "^[a-z0-9-]+$" },
        "adapter_version": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$" },
        "peer_kind": { "type": "string", "pattern": "^[a-z]+$" },
        "capabilities": {
          "type": "array",
          "items": { "enum": ["SESSION", "STREAM", "GRACEFUL_CANCEL"] }
        },
        "transports": {
          "type": "array",
          "items": { "enum": ["PIPE", "PTY"] }
        },
        "readiness_probe_id": { "type": "string" },
        "usage_provider_id": { "type": "string" }
      }
    },
    "execution": {
      "type": "object",
      "additionalProperties": false,
      "required": ["executable", "templates", "env_policy"],
      "properties": {
        "executable": {
          "type": "object",
          "additionalProperties": false,
          "required": ["resolution_rule", "target"],
          "properties": {
            "resolution_rule": { "enum": ["absolute", "sibling", "path"] },
            "target": { "type": "string" }
          }
        },
        "templates": {
          "type": "object",
          "additionalProperties": false,
          "required": ["start"],
          "properties": {
            "start": { "$ref": "#/$defs/invocation_template" },
            "resume": { "$ref": "#/$defs/invocation_template" }
          }
        },
        "env_policy": {
          "type": "object",
          "additionalProperties": false,
          "required": ["inherit", "set"],
          "properties": {
            "inherit": { "type": "array", "items": { "type": "string" } },
            "set": {
              "type": "object",
              "additionalProperties": { "type": "string" }
            }
          }
        }
      }
    },
    "engine": {
      "type": "object",
      "additionalProperties": false,
      "required": ["engine_id", "options"],
      "properties": {
        "engine_id": { "type": "string", "enum": ["builtin:json-claude-v1", "builtin:jsonl-codex-v1", "builtin:json-agy-v1", "builtin:pty-legacy-v1"] },
        "options": {
          "type": "object",
          "description": "Explicit finite typed options per engine.",
          "oneOf": [
            {
              "title": "Builtin CLI Regex Options",
              "properties": { 
                "success_regex": { "type": "string" },
                "error_regex": { "type": "string" }
              },
              "additionalProperties": false
            },
            {
              "title": "Builtin SSE Options",
              "properties": { 
                "enforce_strict_json": { "type": "boolean" } 
              },
              "additionalProperties": false
            },
            {
              "title": "Empty Options",
              "properties": {},
              "additionalProperties": false
            }
          ]
        }
      }
    },
    "profiles": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["profile_id", "profile_class", "supports_reasoning_effort", "transport", "prompt_policy"],
        "properties": {
          "profile_id": { "type": "string" },
          "profile_class": { "type": "string" },
          "supports_reasoning_effort": { "type": "boolean" },
          "transport": { "enum": ["PIPE", "PTY"] },
          "prompt_policy": {
            "type": "object",
            "additionalProperties": false,
            "required": ["policy_id", "max_inline_utf8_bytes", "artifact_reference_supported"],
            "properties": {
              "policy_id": { "type": "string" },
              "max_inline_utf8_bytes": { "type": "integer", "minimum": 0 },
              "artifact_reference_supported": { "type": "boolean" }
            }
          }
        }
      }
    }
  },
  "$defs": {
    "invocation_template": {
      "type": "object",
      "additionalProperties": false,
      "required": ["argv", "cwd"],
      "properties": {
        "argv": { "type": "array", "items": { "type": "string" } },
        "cwd": { "type": "string" },
        "stdin": { "type": "string" },
        "artifacts": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["artifact_id", "placeholder", "access_mode", "lifecycle"],
            "properties": {
              "artifact_id": { "type": "string" },
              "placeholder": { "type": "string" },
              "access_mode": { "type": "string" },
              "lifecycle": { "type": "string" }
            }
          }
        }
      }
    }
  }
}
```

## 4. Executable Admission, ACL Evaluation, and Transitive Binding Model

To eliminate TOCTOU, unauthenticated tampering, and wrapper-only binding vulnerabilities, manifest admission strictly resolves, validates, and cryptographically binds the complete transitive execution graph.

### 4.1. Semantic Validation Rules at Admission
In addition to JSON Schema structural checks and absolute path resolution:
1. **Start Template Guard**: The `execution.templates.start` object MUST contain a prompt placeholder (`{prompt_content}` or `{prompt_reference}`) within its `argv` or `stdin` fields. If missing, the manifest is hard-rejected at admission time.
2. **Resume Template Guard**: If `execution.templates.resume` is provided (mandatory if `capabilities` includes `SESSION`), it MUST contain a session placeholder (e.g., `{session.external_session_id}`) in its `argv` or `stdin` fields. If missing, the manifest is hard-rejected.

### 4.2. Reconciled Windows ACL Evaluation (Non-World-Writable NTFS Enforcement)
Empirical investigation of live Windows environments (`P:\`, `D:\`, `%APPDATA%`, `%LOCALAPPDATA%`) demonstrates that default NTFS inheritance grants `NT AUTHORITY\Authenticated Users:(OI)(CI)(M)`. Requiring denial of Authenticated Users Modify access would reject 100% of standard non-elevated developer and portable installations.

The admission engine enforces the following real-world security threshold:
1. **Local NTFS Filesystem**: Target files and directories must reside on a local NTFS volume supporting access control lists.
2. **World-Writable & Anonymous Denial**:
   * Security Principal `Everyone` (`S-1-1-0`) must NOT have Write Data (`WD`), Append Data (`AD`), Write Attributes (`WA`), Write Extended Attributes (`WEA`), Delete (`DE`), Modify (`M`), or Full Control (`F`). At most Read & Execute `(RX)` is permitted.
   * `ANONYMOUS LOGON` (`S-1-5-7`) and `BUILTIN\Guests` (`S-1-5-32-546`) must have no write or modify permissions.
3. **Authorized Ownership & Authenticated Users**:
   * Directory ownership must belong to `BUILTIN\Administrators`, `NT AUTHORITY\SYSTEM`, or the active user account SID.
   * `NT AUTHORITY\Authenticated Users:(M)` is explicitly permitted on local workstation installs.
4. **Reparse Point / Junction Safety**:
   * No directory component in the path may traverse an unverified symlink, volume mount point, or junction point.

### 4.3. Transitive Executable Chain Resolution & Hashing
Admission does NOT stop at top-level `.cmd` wrappers:
1. **Static Trampoline Tracing**: If the entrypoint is a `.cmd`/`.bat` wrapper, the parser analyzes the script to resolve the target interpreter (`node.exe` resolved on `PATH` at admission time) and script target (`codex.js`). If the script invokes a vendor native binary (e.g. `claude.exe`, `codex.exe`), the native binary is resolved.
2. **Transitive Chain Structuring**:
   Every file in the transitive execution chain is assigned a role (`ENTRYPOINT_WRAPPER`, `INTERPRETER`, `SCRIPT`, `NATIVE_BINARY`, `HELPER_BINARY`) and recorded with its canonical absolute path, byte length, and SHA-256 digest.
3. **Aggregate Chain Digest**:
   The aggregate chain digest is computed over the canonically sorted sequence of `(role, canonical_path, sha256)`:
   $$\text{Digest} = \text{SHA256}\left( \bigoplus_{i} \left( \text{Role}_i \mathbin{\Vert} \text{":"} \mathbin{\Vert} \text{Path}_i \mathbin{\Vert} \text{":"} \mathbin{\Vert} \text{SHA256}_i \mathbin{\Vert} \text{"\n"} \right) \right)$$
4. **Pre-Spawn Revalidation**:
   Immediately before invoking `subprocess.Popen`:
   * The manifest canonical JSON AST hash is checked.
   * The Python engine code digest is checked.
   * Every file in the `transitive_executable_chain` is `stat`'d and re-hashed.
   * `PATH` is **never re-resolved** at spawn time; only the pinned absolute paths are executed.

---

## 5. Collision Algorithm, Canonical Normalization, and Atomic Snapshots

To prevent registry poisoning, profile hijacking, and platform-specific casing/alias collisions:

### 5.1. Key Normalization Algorithm: `normalize_key(s: str) -> str`
1. **Unicode Normalization Form C**: `unicodedata.normalize('NFC', s)`
2. **Unicode Case-Folding**: `s.casefold()`
3. **Trailing Whitespace/Dot Stripping**: `s.rstrip('. ')`
4. **Executable Extension Stripping**: Strip recognized extensions (`.exe`, `.cmd`, `.bat`, `.com`, `.ps1`, `.vbs`, `.js`).

### 5.2. Claim Space Extraction & Collision Rules
For every manifest $M_i$:
* Extract normalized `adapter_id`, `peer_kind`, `profile_ids`, `shim_names`, and aliases.
* Compute canonical AST digest: `SHA256(canonical_json(M_i))`.
* **Collision Verdict**: A collision occurs if multiple manifests claim the same normalized key or share the same AST digest under different filenames.

### 5.3. Atomic Snapshot Publication & Reader Synchronization
1. **Monotonic Generation**: Monotonically increasing 64-bit integer `registry_generation`.
2. **Candidate Staging**: Candidate manifests are loaded into an isolated memory buffer.
3. **Atomic Rejection**: If ANY manifest fails JSON schema, ACL checks, semantic template checks, transitive chain resolution, or triggers a collision, the **entire candidate snapshot is rejected**.
4. **RCU Pointer Swap**: On success, the new snapshot is wrapped in `PublishedRegistry(generation=G+1, timestamp=now, adapters=...)` and published via an atomic pointer store (`active_registry_ref.store()`).
5. **Reader Immunity**: In-flight executions maintain pinned references to their active `PublishedRegistry` and `AdmissionReceipt`, preventing tearing across reloads.

---

## 6. Worked Examples

### 6.1. claude-adapter.json
```json
{
  "manifest_version": "2.0.0",
  "status": "active",
  "adapter": {
    "adapter_id": "claude-peer",
    "adapter_version": "1.0.0",
    "peer_kind": "cc",
    "capabilities": ["SESSION"],
    "transports": ["PIPE"],
    "readiness_probe_id": "process-exit-zero"
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "claude.cmd"
    },
    "templates": {
      "start": {
        "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json"],
        "cwd": "{workspace_scope}"
      },
      "resume": {
        "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json", "--resume", "{session.external_session_id}"],
        "cwd": "{workspace_scope}"
      }
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE"],
      "set": {}
    }
  },
  "engine": {
    "engine_id": "builtin:json-claude-v1",
    "options": { "enforce_strict_json": true }
  },
  "profiles": [
    {
      "profile_id": "cc.standard",
      "profile_class": "tier",
      "supports_reasoning_effort": false,
      "transport": "PIPE",
      "prompt_policy": {
        "policy_id": "cc-standard-policy",
        "max_inline_utf8_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```

### 6.2. codex-adapter.json
```json
{
  "manifest_version": "2.0.0",
  "status": "active",
  "adapter": {
    "adapter_id": "codex-peer",
    "adapter_version": "1.0.0",
    "peer_kind": "cx",
    "capabilities": ["SESSION", "STREAM"],
    "transports": ["PIPE"],
    "readiness_probe_id": "process-exit-zero"
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "codex.cmd"
    },
    "templates": {
      "start": {
        "argv": ["{executable}", "exec", "--json", "{prompt_content}"],
        "cwd": "{workspace_scope}"
      },
      "resume": {
        "argv": ["{executable}", "exec", "resume", "--json", "{session.external_session_id}", "{prompt_content}"],
        "cwd": "{workspace_scope}"
      }
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE", "APPDATA"],
      "set": {}
    }
  },
  "engine": {
    "engine_id": "builtin:jsonl-codex-v1",
    "options": {}
  },
  "profiles": [
    {
      "profile_id": "cx.standard",
      "profile_class": "tier",
      "supports_reasoning_effort": false,
      "transport": "PIPE",
      "prompt_policy": {
        "policy_id": "cx-standard-policy",
        "max_inline_utf8_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```

### 6.3. agy-adapter.json
```json
{
  "manifest_version": "2.0.0",
  "status": "active",
  "adapter": {
    "adapter_id": "agy-peer",
    "adapter_version": "1.0.0",
    "peer_kind": "ag",
    "capabilities": ["SESSION"],
    "transports": ["PIPE"],
    "readiness_probe_id": "process-exit-zero"
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "agy.exe"
    },
    "templates": {
      "start": {
        "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json"],
        "cwd": "{workspace_scope}",
        "stdin": "DEVNULL"
      },
      "resume": {
        "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json", "--conversation", "{session.external_session_id}"],
        "cwd": "{workspace_scope}",
        "stdin": "DEVNULL"
      }
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA"],
      "set": {}
    }
  },
  "engine": {
    "engine_id": "builtin:json-agy-v1",
    "options": {}
  },
  "profiles": [
    {
      "profile_id": "ag.standard",
      "profile_class": "tier",
      "supports_reasoning_effort": true,
      "transport": "PIPE",
      "prompt_policy": {
        "policy_id": "ag-standard-policy",
        "max_inline_utf8_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```

## 7. Honest Gaps & Unexpressible Features

Despite this rigorous translation, certain runtime interaction patterns in the real world *genuinely cannot be expressed* through this purely declarative schema:

1. **PTY Interactive State Machines (Legacy/Test Tools)**: A true interactive PTY tool does not accept a one-shot prompt via argv. It launches into an interactive shell, emits a prompt character, and requires input to be typed into `stdin` at the right moment. The declarative manifest cannot express "wait for sequence X, send Y". For these explicitly-opt-in transport choices, a dedicated Engine (e.g. `builtin:pty-legacy-v1`) is forced to hardcode this state machine logic in Python. The manifest simply points to that Engine and provides the `argv` for spawn.
2. **Graceful Cancel Delivery**: `InvocationPlan` inherently omits the graceful cancel recipe because it often involves out-of-band signals (e.g. `SIGINT` or specific terminal control sequences on Windows). The manifest lacks a way to declare "send Ctrl+C on cancel". The Engine entirely owns implementing the cancel mechanism for its target tool.
3. **Complex Artifact Generation**: For tools that require dynamically generated artifact config files (e.g., writing a `.toml` on the fly containing multiple dynamically routed options before running the command), `execution.templates.*.artifacts` can only provide simple placeholder injection. Any complex logic must be synthesized by the Engine before the process spawns.
