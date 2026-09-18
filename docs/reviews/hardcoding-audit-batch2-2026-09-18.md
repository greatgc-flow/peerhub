# Code Quality Audit: peerhub/persistence, peerhub/governance, peerhub/cli

## Category A: Hardcoded Literals

### 1. [HIGH] Protocol Version String
**Description**: The string `"protocol-v2"` representing the core governance policy revision is hardcoded across almost all governance modules when assembling `MutationRequest` structures. If this protocol needs to be upgraded, developers would have to find and replace these raw strings in 10 different files manually, easily leading to silent policy drift.
**Occurrences**:
- `peerhub/governance/activity.py:98`
- `peerhub/governance/artifact_records.py:92`
- `peerhub/governance/consensus.py:105`
- `peerhub/governance/consensus.py:773`
- `peerhub/governance/directives.py:157`
- `peerhub/governance/feedback.py:70`
- `peerhub/governance/file_locks.py:66`
- `peerhub/governance/lessons.py:305`
- `peerhub/governance/operational_errors.py:60`
- `peerhub/governance/rooms.py:1259`

### 2. [HIGH] Persistence SQL Table Names
**Description**: Database table names are directly hardcoded into multiline raw SQL strings rather than referencing a centralized set of constants. When table schemas change or are renamed, queries referencing these string tables will silently fail during execution rather than initialization.
**Occurrences** (Representative subset):
- `dispatch_requests`: `peerhub/persistence/dispatch_context.py:121`, `peerhub/persistence/restore_authority.py:17`, `peerhub/persistence/sqlite_dispatch.py:1009`
- `dispatch_attempts`: `peerhub/persistence/restore_authority.py:18`, `peerhub/persistence/sqlite_dispatch.py:2097`
- `effect_deliveries`: `peerhub/persistence/restore_authority.py:40`, `peerhub/persistence/sqlite_governance.py:402`
- `consumer_offsets`: `peerhub/persistence/sqlite_events.py:102`, `peerhub/persistence/sqlite_governance.py:461`

### 3. [HIGH] Governance State Machine Strings (`ACTIVE`, `FAILED`)
**Description**: Target lifecycle and completion states are manipulated as bare string literals (`"ACTIVE"`, `"FAILED"`) instead of authoritative enums across the governance module.
**Occurrences**:
- `"ACTIVE"`: `peerhub/governance/activity.py:52`, `peerhub/governance/directives.py:83`, `peerhub/governance/directives.py:114`, `peerhub/governance/lessons.py:196`, `peerhub/governance/lessons.py:206`
- `"FAILED"`: `peerhub/governance/activity.py:33`, `peerhub/governance/lessons.py:160`, `peerhub/governance/tasks.py:221`, `peerhub/governance/tasks.py:252`


## Category B: Duplicated Logic

### 4. [MEDIUM] MutationRequest Submission Boilerplate
**Description**: The exact boilerplate logic to construct a `MutationRequest` (generating IDs, setting `command_type`, `client_id`, `policy_revision`, submitting to `self._broker`) is duplicated as a private `_submit` helper across 9 governance services.
**Behavioral differences**: The prefix used for ID generation (`self._ids.new_id("...")`) changes based on the service name, and `peerhub/governance/rooms.py` allows for an overriding `correlation_id` argument.
**Occurrences**:
- `peerhub/governance/artifact_records.py:69`
- `peerhub/governance/consensus.py:753`
- `peerhub/governance/directives.py:139`
- `peerhub/governance/feedback.py:51`
- `peerhub/governance/file_locks.py:47`
- `peerhub/governance/lessons.py:287`
- `peerhub/governance/operational_errors.py:37`
- `peerhub/governance/rooms.py:1235`
- `peerhub/governance/tasks.py:291`

### 5. [LOW] Capability Tier Parsing Boilerplate
**Description**: Boilerplate function `_required_capability_tier_from_stored` that ensures a stored raw db value is a string, then parses it via `CapabilityTier[raw]`, wrapped in a `KeyError` try/except block.
**Behavioral differences**: `sqlite_dispatch.py` raises `RuntimeError("stored request is missing required_capability_tier")` while `sqlite_routing.py` raises `RuntimeError("stored route decision is missing required_capability_tier")`.
**Occurrences**:
- `peerhub/persistence/sqlite_dispatch.py:93`
- `peerhub/persistence/sqlite_routing.py:184`

### 6. [LOW] Required Text Extraction Boilerplate
**Description**: A `_required_text` helper function fetching a value from a state dict, validating it's a string, ensuring `.strip()` is not empty, and throwing an `InvalidMutationError` otherwise. 
**Occurrences**:
- `peerhub/governance/consensus.py:798`
- `peerhub/governance/invariant_requests.py:168`

### 7. [LOW] CLI Argument Setup
**Description**: Identical `parser.add_argument(...)` boilerplate configuring common flags like `--workspace` and `--json` rather than using a parent parser feature.
**Behavioral differences**: Sometimes `--json` is described with `help="Emit JSON"`, sometimes `help="Emit JSON output"`.
**Occurrences**:
- `peerhub/cli/commands/daily.py:37` (`--workspace`)
- `peerhub/cli/commands/daily.py:41` (`--json`)
- `peerhub/cli/commands/daily.py:58` (`--workspace`)
- `peerhub/cli/commands/daily.py:62` (`--json`)
- `peerhub/cli/commands/daily.py:86` (`--json`)
- `peerhub/cli/commands/setup.py:53` (`--json`)
- `peerhub/cli/commands/setup.py:128` (`--json`)
