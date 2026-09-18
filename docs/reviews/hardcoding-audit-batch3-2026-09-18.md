# Code Quality Audit: peerhub/health, peerhub/telemetry, peerhub/adapters, peerhub/routing, peerhub/core, peerhub/builtins, peerhub/events, peerhub/state, peerhub/config_data

## Category A: Hardcoded Literals

### 1. [MEDIUM] Telemetry Metric Identifiers
**Description**: Quota tracking metric names (`"3p-5h"`, `"3p-weekly"`, `"gemini-5h"`, `"gemini-weekly"`) are hardcoded string literals across the polling and presentation layers. These should be a shared Enum.
**Occurrences**:
- `"3p-5h"`: `peerhub/telemetry/presenter.py:347`, `peerhub/telemetry/quota_polling.py:621`, `peerhub/telemetry/statusline.py:91`
- `"3p-weekly"`: `peerhub/telemetry/presenter.py:348`, `peerhub/telemetry/quota_polling.py:621`, `peerhub/telemetry/statusline.py:92`
- `"gemini-5h"`: `peerhub/telemetry/presenter.py:372`, `peerhub/telemetry/quota_polling.py:620`, `peerhub/telemetry/statusline.py:89`
- `"gemini-weekly"`: `peerhub/telemetry/presenter.py:373`, `peerhub/telemetry/quota_polling.py:620`, `peerhub/telemetry/statusline.py:90`

### 2. [HIGH] API Endpoint / Handshake Identifiers
**Description**: The API endpoints and client identifiers for the third-party LSP quota polling (e.g. `"account/rateLimits/read"`, `"clientInfo"`) are hardcoded in both the credential fetcher and the quota poller.
**Occurrences**:
- `"account/rateLimits/read"`: `peerhub/telemetry/codex_credit.py:177`, `peerhub/telemetry/quota_polling.py:457`
- `"clientInfo"`: `peerhub/telemetry/codex_credit.py:120`, `peerhub/telemetry/quota_polling.py:446`


## Category B: Duplicated Logic

### 3. [MEDIUM] Codex App-Server JSON-RPC Handshake Boilerplate
**Description**: The exact logic sequence for setting up an LSP JSON-RPC stream, writing an `"initialize"` payload with `clientInfo`, waiting for a response, and sending `"initialized"` is completely duplicated between `codex_credit.py` and `quota_polling.py`.
**Behavioral differences**: `codex_credit.py` encapsulates this in a `_CodexAppServerSession` class with threads, timeouts, and exceptions, while `quota_polling.py` implements it inline as bare `proc.stdin.write(...)` calls returning a `_fail_closed` tuple on error.
**Occurrences**:
- `peerhub/telemetry/codex_credit.py:119`
- `peerhub/telemetry/quota_polling.py:445`

### 4. [LOW] String Splitting/Normalization Boilerplate (`_split_canonical_lines`)
**Description**: The adapters copy-paste a function `_split_canonical_lines` that handles `\r\n` to `\n` normalization, strips trailing newlines, and returns a tuple of strings.
**Behavioral differences**: Identical logic across all three adapters.
**Occurrences**:
- `peerhub/adapters/agy_adapter.py:34`
- `peerhub/adapters/claude_adapter.py:37`
- `peerhub/adapters/codex_adapter.py:40`

### 5. [LOW] Adapter Stream Ingestion Boilerplate (`feed()`)
**Description**: Boilerplate `feed` method ensuring a stream hasn't been finalized, validating `chunk` is bytes, appending to `self._chunks`, and returning an empty tuple.
**Behavioral differences**: Identical across `agy` and `claude` adapters.
**Occurrences**:
- `peerhub/adapters/agy_adapter.py:92`
- `peerhub/adapters/claude_adapter.py:95`

### 6. [LOW] Hex/SHA256 String Validation Boilerplate (`_require_sha256_hex`)
**Description**: Logic validating a string length is 64 and contains only hex characters (using the literal `"0123456789abcdef"`).
**Behavioral differences**: Identical logic.
**Occurrences**:
- `peerhub/health/contract.py:34`
- `peerhub/routing/contract.py:41`
- `peerhub/routing/model.py:31`

### 7. [LOW] Tuple Normalization Boilerplate (`_normalize_refs`)
**Description**: Tuple comprehensions mapping raw list values into specific `EvidenceRef` components.
**Behavioral differences**: Identical comprehension block.
**Occurrences**:
- `peerhub/health/contract.py:65`
- `peerhub/routing/contract.py:72`
- `peerhub/telemetry/contract.py:40`


## Cross-Batch Findings

### 8. [HIGH] Hardcoded CLI Executable Names
**Description**: Bare strings for the CLI executable commands (`claude.cmd`, `codex.cmd`) are hardcoded across multiple boundaries (adapters, telemetry, core, dispatch). Upgrading or deploying to Linux would require chasing down multiple arbitrary strings.
**Occurrences**:
- `"claude.cmd"`: `peerhub/adapters/claude_adapter.py:250`, `peerhub/core/binary_resolution.py:23`, `peerhub/dispatch/pipe.py:310`, `peerhub/telemetry/quota_polling.py:175`
- `"codex.cmd"`: `peerhub/adapters/codex_adapter.py:314`, `peerhub/core/binary_resolution.py:24`, `peerhub/dispatch/pipe.py:330`, `peerhub/telemetry/quota_polling.py:190`

### 9. [HIGH] Schema Version (`"1.0.0"`)
**Description**: Despite `peerhub/core/protocol.py` correctly defining a centralized constant `SCHEMA_VERSION = "1.0.0"`, this string literal is manually hardcoded repeatedly in application layer and adapter serialization code rather than being imported.
**Occurrences**:
- `peerhub/core/protocol.py:22` (Definition)
- `peerhub/adapters/agy_adapter.py:73` (Hardcoded)
- `peerhub/adapters/codex_adapter.py:78` (Hardcoded)
- `peerhub/application/broadcast.py:242` (Hardcoded)
- `peerhub/application/direct_ask.py:576` (Hardcoded)

### 10. [MEDIUM] State/Status Enums Treated as Raw Strings
**Description**: Enum members for Availability, Readiness, and Lifecycle states (like `"STALE"`, `"FAILED"`, `"ERROR"`) are used as bare strings instead of their corresponding enum definition/value equivalents across multiple modules in `application/`, `governance/`, and `dispatch/`.
**Occurrences** (Representative):
- `"STALE"`: `peerhub/application/capability_config.py:275`, `peerhub/application/direct_ask.py:164`, `peerhub/core/evidence.py:24`, `peerhub/dispatch/contract.py:142`, `peerhub/health/contract.py:82`
- `"ERROR"`: `peerhub/application/health_revalidation.py:248`, `peerhub/application/room_broadcast.py:63`, `peerhub/cli/commands/setup.py:210`, `peerhub/core/evidence.py:23`

### 11. [LOW] Primitive Validation Guards (`_require_nonnegative`, `_require_positive`)
**Description**: Simple boundary checking boilerplate (`if type(value) is not int or value < 0: raise ValueError(...)`) is copy-pasted across almost every domain contract file instead of using a shared utility function.
**Behavioral differences**: Minor differences in variable naming/exception string format.
**Occurrences**:
- `peerhub/core/evidence.py:27`
- `peerhub/health/contract.py:16`
- `peerhub/routing/contract.py:23`
- `peerhub/telemetry/contract.py:22`
- `peerhub/governance/contract.py:72` (From Batch 2)
- `peerhub/dispatch/capability.py:54` (From Batch 1)
- `peerhub/dispatch/contract.py:182` (From Batch 1)
- `peerhub/adapters/contract.py:43`
- `peerhub/core/protocol.py:360`
