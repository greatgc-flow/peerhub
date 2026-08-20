# Phase 1: Manifest Schema v1.0.0 and Admission Model

## 1. Overview
This design defines the JSON manifest schema and trust model for Peerhub adapters, addressing the R2-02 and R2-03 critique findings. It provides a purely declarative manifest for configuration (argv, environment policy, feature subsets) while isolating Turing-complete decoder logic into a separately reviewed, bounded engine mechanism.

## 2. Gap Analysis and Resolutions
When mapping the three real adapter implementations (`claude`, `codex`, `agy`) to a declarative schema, two primary gaps emerged where a generic data DSL is insufficiently expressive:

1. **Output Decoding and Error Normalization**: The adapters parse materially different stream formats (`codex` uses JSONL, `claude` and `agy` use single JSON envelopes) and map disparate vendor-specific error codes to standard core `ErrorCode` values. Also, they extract session identities differently (Codex from `thread.started`, Agy from `conversation_id`, Claude expects caller pre-generation).
   * **Resolution**: To prevent the manifest from becoming an unreviewed Turing-complete DSL, this is not fudged. The manifest uses a bounded `decoder.engine` string (e.g., `builtin:jsonl-codex-v1`) that maps strictly to a compiled, installed Python engine class. Genuinely new decoders require a separately reviewed adapter package as mandated by cx's R2-02 boundary rule.
2. **Evidence-to-Prompt Inlining**: Current adapters manually inject `<large output was...>` summaries for evidence payloads exceeding limits, handle hashing, and manage URL placeholders.
   * **Resolution**: This logic belongs in Peerhub Core. Core will format the evidence payloads according to the `prompt_policy` byte limits and supply a single, final `{prompt}` string. The manifest is relieved of branching formatting logic.

## 3. The JSON Schema Definition
The following is the precise JSON Schema draft 2020-12 definition.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://peerhub.local/schema/adapter-manifest/v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "manifest_version",
    "status",
    "adapter",
    "execution",
    "decoder",
    "profiles"
  ],
  "properties": {
    "manifest_version": { "const": "1.0.0" },
    "status": {
      "enum": ["active", "inactive"],
      "description": "Explicit activation toggle."
    },
    "adapter": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "version", "peer_kind", "capabilities"],
      "properties": {
        "id": { "type": "string", "pattern": "^[a-z0-9-]+$" },
        "version": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$" },
        "peer_kind": { "type": "string", "pattern": "^[a-z]+$" },
        "capabilities": {
          "type": "array",
          "items": { "enum": ["SESSION", "STREAM", "GRACEFUL_CANCEL"] }
        }
      }
    },
    "execution": {
      "type": "object",
      "additionalProperties": false,
      "required": ["executable", "argv_templates", "env_policy"],
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
        "argv_templates": {
          "type": "object",
          "additionalProperties": false,
          "required": ["start"],
          "properties": {
            "start": { "type": "array", "items": { "type": "string" } },
            "resume": { "type": "array", "items": { "type": "string" } }
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
    "decoder": {
      "type": "object",
      "additionalProperties": false,
      "required": ["engine"],
      "properties": {
        "engine": { "enum": ["builtin:json-claude-v1", "builtin:jsonl-codex-v1", "builtin:json-agy-v1"] },
        "engine_options": { "type": "object" }
      }
    },
    "profiles": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "class", "supports_reasoning", "prompt_policy"],
        "properties": {
          "id": { "type": "string" },
          "class": { "type": "string" },
          "supports_reasoning": { "type": "boolean" },
          "prompt_policy": {
            "type": "object",
            "additionalProperties": false,
            "required": ["policy_id", "max_inline_bytes", "artifact_reference_supported"],
            "properties": {
              "policy_id": { "type": "string" },
              "max_inline_bytes": { "type": "integer", "minimum": 0 },
              "artifact_reference_supported": { "type": "boolean" }
            }
          }
        }
      }
    }
  }
}
```

## 4. Executable Admission and Binding Model
To eliminate TOCTOU and privilege escalation vectors, binding strictly separates declaration from execution.

### 4.1. Validation Steps at Admission
1. **File Constraints**: Max file size is 64KB, max depth is 10. Encoding must be strict UTF-8 (BOM rejected).
2. **Strict JSON**: The parser operates in strict mode; duplicate JSON keys and unknown fields are hard-rejected.
3. **Canonical Digest**: The hash is computed on the strictly serialized JSON AST (keys sorted, no whitespace).
4. **Absolute Resolution**: The `resolution_rule` determines how `target` binds:
   * `absolute`: The `target` must already be absolute.
   * `sibling`: Resolved relative to the manifest file's directory.
   * `path`: Looked up via the `PATH` environment variable strictly *at admission time*.
5. **Security Constraints**: The resolved absolute path must not traverse a symlink, junction, or reparse point. It must reside on a local NTFS volume where directory ACLs deny unprivileged writes.
6. **Immutable Binding Record**: On success, the system generates an immutable `AdmissionReceipt` tracking:
   * `bound_absolute_path`: The resolved executable path.
   * `executable_hash`: SHA256 file hash of the executable.
   * `manifest_canonical_hash`: SHA256 of the manifest AST.
   * `observed_version`: The manifest version.

### 4.2. Revalidation Before Spawn
Immediately before invoking `subprocess.Popen`:
1. The `bound_absolute_path` from the `AdmissionReceipt` is `stat`'d. `PATH` is **never** re-resolved.
2. If the file is missing or its identity hash differs from `executable_hash`, the launch is aborted.

### 4.3. Execution Semantics
* **Workspace Canonicalization**: The request's workspace scope is canonicalized and verified against authorized roots before invocation.
* **Environment**: Only variables explicitly named in `env_policy.inherit` are copied from the parent process. Variables in `env_policy.set` are hardcoded. Inheritance of high-risk vars (like `PYTHONPATH`) without explicit authorization is blocked.
* **Precise Placeholders**:
  * Substitutions occur as *whole tokens only*. A list element must be exactly `"{prompt}"`, not `"--arg={prompt}"`.
  * `argv[0]` is exclusively reserved for `"{executable}"`, which resolves strictly to the `bound_absolute_path`. No other placeholders are allowed in `argv[0]`.
  * No shell escaping is performed; execution strictly uses list-mode execution (e.g., `subprocess` without `shell=True`).
  * Tokens can appear at most once. Byte limits are enforced against the 32,767 character OS argv limit.
  * For display redaction, the exact tokens holding `{prompt}` and `{session.id}` are redacted to `<redacted>`.
* **Clean Separation**: The manifest only defines the static descriptor and static profile topologies. It contains no default models, aliases, or active API keys; those belong exclusively to the operational `PeerProfileBinding`.

## 5. Collision Algorithm and Atomic Snapshots
Implementing cx's atomic-candidate-snapshot rule:
1. **Candidate Snapshot**: On cold start or explicit configuration reload, all `.json` files in the registry are parsed into a candidate snapshot.
2. **Claim Extraction**: The complete claim set (`adapter_id`, `peer_kind`, profile IDs) is aggregated. Windows case folding, Unicode normalization, and executable-extension normalization are applied.
3. **Collision Detection**: 
   * A collision exists if multiple manifests claim the same `adapter_id`, `peer_kind`, or profile ID.
   * Identical duplicate manifests (same AST, different filenames) also trigger a collision.
4. **Atomic Rejection**: If *any* collision is found across the candidate scope, the *entire* candidate snapshot is rejected. There is no partial registry load.
5. **State Behavior**:
   * **Hot Reload**: The previous immutable snapshot remains active, and a stable diagnostic is emitted.
   * **Cold Start**: The application aborts. It will never silently guess a winner.

## 6. Worked Examples

### 6.1. claude-adapter.json
```json
{
  "manifest_version": "1.0.0",
  "status": "active",
  "adapter": {
    "id": "claude-peer",
    "version": "1.0.0",
    "peer_kind": "cc",
    "capabilities": ["SESSION"]
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "claude.cmd"
    },
    "argv_templates": {
      "start": ["{executable}", "-p", "{prompt}", "--output-format", "json"],
      "resume": ["{executable}", "-p", "{prompt}", "--output-format", "json", "--resume", "{session.id}"]
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE"],
      "set": {}
    }
  },
  "decoder": {
    "engine": "builtin:json-claude-v1"
  },
  "profiles": [
    {
      "id": "cc.standard",
      "class": "tier",
      "supports_reasoning": false,
      "prompt_policy": {
        "policy_id": "cc-standard-policy",
        "max_inline_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```

### 6.2. codex-adapter.json
```json
{
  "manifest_version": "1.0.0",
  "status": "active",
  "adapter": {
    "id": "codex-peer",
    "version": "1.0.0",
    "peer_kind": "cx",
    "capabilities": ["SESSION", "STREAM"]
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "codex.cmd"
    },
    "argv_templates": {
      "start": ["{executable}", "exec", "--json", "{prompt}"],
      "resume": ["{executable}", "exec", "resume", "--json", "{session.id}", "{prompt}"]
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE", "APPDATA"],
      "set": {}
    }
  },
  "decoder": {
    "engine": "builtin:jsonl-codex-v1"
  },
  "profiles": [
    {
      "id": "cx.standard",
      "class": "tier",
      "supports_reasoning": false,
      "prompt_policy": {
        "policy_id": "cx-standard-policy",
        "max_inline_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```

### 6.3. agy-adapter.json
```json
{
  "manifest_version": "1.0.0",
  "status": "active",
  "adapter": {
    "id": "agy-peer",
    "version": "1.0.0",
    "peer_kind": "ag",
    "capabilities": ["SESSION"]
  },
  "execution": {
    "executable": {
      "resolution_rule": "path",
      "target": "agy.exe"
    },
    "argv_templates": {
      "start": ["{executable}", "-p", "{prompt}", "--output-format", "json"],
      "resume": ["{executable}", "-p", "{prompt}", "--output-format", "json", "--conversation", "{session.id}"]
    },
    "env_policy": {
      "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA"],
      "set": {}
    }
  },
  "decoder": {
    "engine": "builtin:json-agy-v1"
  },
  "profiles": [
    {
      "id": "ag.standard",
      "class": "tier",
      "supports_reasoning": true,
      "prompt_policy": {
        "policy_id": "ag-standard-policy",
        "max_inline_bytes": 1000000,
        "artifact_reference_supported": false
      }
    }
  ]
}
```
