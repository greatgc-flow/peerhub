# Code Quality Audit: peerhub/application and peerhub/dispatch

## Category A: Hardcoded Literals

### 1. [HIGH] Dispatch Contract Payload Keys
**Description**: The core dictionary keys for dispatch requests are hardcoded identically across the dispatch contract definitions and the application-layer command unpacking. A typo in any of these will cause silent data loss or schema validation failures.
**Occurrences**:
- `"client_id"`: `peerhub/application/commands/__init__.py:135`, `peerhub/application/handlers/dispatch_builtins.py:452`, `peerhub/dispatch/contract.py:469`, `peerhub/dispatch/contract.py:510`, `peerhub/dispatch/contract.py:558`, `peerhub/dispatch/contract.py:635`
- `"command_type"`: `peerhub/application/commands/__init__.py:139`, `peerhub/application/handlers/dispatch_builtins.py:456`, `peerhub/dispatch/contract.py:511`, `peerhub/dispatch/contract.py:560`, `peerhub/dispatch/contract.py:639`
- `"idempotency_key"`: `peerhub/application/commands/__init__.py:140`, `peerhub/application/handlers/dispatch_builtins.py:457`, `peerhub/dispatch/contract.py:512`, `peerhub/dispatch/contract.py:561`, `peerhub/dispatch/contract.py:640`
- `"payload_digest"`: `peerhub/application/commands/__init__.py:141`, `peerhub/application/handlers/dispatch_builtins.py:458`, `peerhub/dispatch/contract.py:448`, `peerhub/dispatch/contract.py:451`, `peerhub/dispatch/contract.py:487`, `peerhub/dispatch/contract.py:490`, `peerhub/dispatch/contract.py:529`, `peerhub/dispatch/contract.py:532`, `peerhub/dispatch/contract.py:579`, `peerhub/dispatch/contract.py:582`
- `"expected_policy_revision"`: `peerhub/application/commands/__init__.py:143`, `peerhub/application/handlers/dispatch_builtins.py:460`, `peerhub/dispatch/contract.py:678`

### 2. [HIGH] Cancellation Stage Signals
**Description**: The tree cancellation lifecycle signals are hardcoded string literals in the controller rather than referencing the canonical enum/constants from the process module.
**Occurrences**:
- `"SOFT_CANCEL"`: `peerhub/dispatch/process.py:76`, `peerhub/dispatch/process.py:87`, `peerhub/dispatch/tree_controller.py:275`
- `"TERMINATE_TREE"`: `peerhub/dispatch/process.py:77`, `peerhub/dispatch/process.py:88`, `peerhub/dispatch/tree_controller.py:332`, `peerhub/dispatch/tree_controller.py:341`
- `"KILL_TREE"`: `peerhub/dispatch/process.py:78`, `peerhub/dispatch/process.py:89`, `peerhub/dispatch/tree_controller.py:379`, `peerhub/dispatch/tree_controller.py:419`

### 3. [MEDIUM] Command Routing Method Strings
**Description**: The explicit string literals used for command method routing are duplicated across their Command definition classes and their respective handler registrations.
**Occurrences**:
- `"coordination.session.open"`: `peerhub/application/commands/sessions.py:13`, `peerhub/application/handlers/duty.py:423`
- `"coordination.session.close"`: `peerhub/application/commands/sessions.py:41`, `peerhub/application/handlers/duty.py:442`
- `"coordination.session.heartbeat"`: `peerhub/application/commands/sessions.py:69`, `peerhub/application/handlers/duty.py:461`
- `"coordination.task.checkpoint"`: `peerhub/application/commands/tasks.py:13`, `peerhub/application/handlers/tasks.py:108`
- `"coordination.task.status"`: `peerhub/application/commands/tasks.py:36`, `peerhub/application/handlers/tasks.py:129`
- `"coordination.task.failover"`: `peerhub/application/commands/tasks.py:50`, `peerhub/application/handlers/tasks.py:139`
- `"governance.approval.request"`: `peerhub/application/commands/tasks.py:67`, `peerhub/application/handlers/tasks.py:170`
- `"dispatch.admit"`: `peerhub/application/commands/__init__.py:59`, `peerhub/application/handlers/dispatch_builtins.py:348`

### 4. [HIGH] Session and Duty Payload Keys
**Description**: Payload property keys for session coordination and duty mapping are hardcoded in both command struct definitions and payload handlers.
**Occurrences**:
- `"session_generation"`: `peerhub/application/commands/duty.py:75`, `peerhub/application/commands/sessions.py:54`, `peerhub/application/handlers/duty.py:141`, `peerhub/application/handlers/duty.py:230`, `peerhub/application/handlers/duty.py:378`, `peerhub/application/handlers/duty.py:392`, `peerhub/application/handlers/duty.py:414`
- `"actor_principal_id"`: `peerhub/application/commands/duty.py:77`, `peerhub/application/commands/sessions.py:27`, `peerhub/application/commands/sessions.py:57`, `peerhub/application/handlers/duty.py:143`, `peerhub/application/handlers/duty.py:154`, `peerhub/application/handlers/duty.py:367`, `peerhub/application/handlers/duty.py:381`, `peerhub/application/handlers/duty.py:395`, `peerhub/application/handlers/duty.py:408`
- `"session_fingerprint"`: `peerhub/application/commands/sessions.py:30`, `peerhub/application/handlers/duty.py:370`, `peerhub/application/handlers/duty.py:413`
- `"heartbeat_timeout_ms"`: `peerhub/application/commands/sessions.py:31`, `peerhub/application/commands/sessions.py:75`, `peerhub/application/handlers/duty.py:371`, `peerhub/application/handlers/duty.py:398`

### 5. [LOW] Lifecycle ID Prefixes
**Description**: Magic string prefixes for generating structural IDs are copy-pasted locally instead of using shared format constants.
**Occurrences**:
- `"outbox-event"`: `peerhub/dispatch/admission.py:366`, `peerhub/dispatch/admission.py:546`, `peerhub/dispatch/attempt_lifecycle.py:167`, `peerhub/dispatch/attempt_lifecycle.py:177`, `peerhub/dispatch/attempt_lifecycle.py:314`, `peerhub/dispatch/attempt_lifecycle.py:452`, `peerhub/dispatch/attempt_lifecycle.py:512`, `peerhub/dispatch/attempt_lifecycle.py:605`, `peerhub/dispatch/attempt_lifecycle.py:610`
- `"capability-lease"`: `peerhub/dispatch/admission.py:495`, `peerhub/dispatch/retry_authorization.py:823`


## Category B: Duplicated Logic

### 6. [LOW] Required String Parameter Validation
**Description**: An identical routine extracting a parameter from a dictionary/envelope and asserting it is a string (`if not isinstance(value, str): raise ValueError(...)`) is independently redefined 9 times across 8 handler files. Behavior and exception generation are completely identical.
**Occurrences**:
- `peerhub/application/handlers/alerts.py:39` (`required_text`)
- `peerhub/application/handlers/consensus.py:213` (`proposal_text`)
- `peerhub/application/handlers/duty.py:69` (`text`)
- `peerhub/application/handlers/duty.py:345` (`text`)
- `peerhub/application/handlers/feedback.py:39` (`required_text`)
- `peerhub/application/handlers/leadership.py:51` (`required_text`)
- `peerhub/application/handlers/operational_errors.py:36` (`required_text`)
- `peerhub/application/handlers/peers.py:113` (`required_text`)
- `peerhub/application/handlers/roles.py:42` (`required_text`)

### 7. [LOW] Optional String Parameter Validation
**Description**: A routine for extracting an optional string parameter with identical type checks. **Behavioral note:** `feedback.py`, `peers.py`, and `roles.py` return `None` if absent, whereas `duty.py` and `leadership.py` substitute `""` (empty string) as a default.
**Occurrences**:
- Returns None: `peerhub/application/handlers/feedback.py:45`, `peerhub/application/handlers/peers.py:74`, `peerhub/application/handlers/roles.py:48`
- Returns "": `peerhub/application/handlers/duty.py:87`, `peerhub/application/handlers/leadership.py:57`

### 8. [LOW] String Sequence Parameter Validation
**Description**: Identical validation logic that iterates through a parameter ensuring it is a tuple/list of strings, throwing ValueError otherwise.
**Occurrences**:
- `peerhub/application/handlers/consensus.py:60` (`string_tuple`)
- `peerhub/application/handlers/lessons.py:75` (`strings`)
- `peerhub/application/handlers/tasks.py:55` (`strings`)

### 9. [MEDIUM] Dispatch Unit Lease Closure Boilerplate
**Description**: A 6-line block of logic calling `unit.get_lease(...)`, checking for `None`, raising a `RecordNotFoundError`, and asserting specific pyright ignores.
**Occurrences**:
- `peerhub/dispatch/attempt_lifecycle.py:669` (`_close_lease_in_unit`)
- `peerhub/dispatch/session_lease.py:215` (`_close_lease_in_unit`)

### 10. [LOW] Non-negative Integer Guard
**Description**: Identical explicit `type(value) is not int or value < 0` bounds checking for configuration parameters.
**Occurrences**:
- `peerhub/dispatch/capability.py:54` (`_require_nonnegative_int`)
- `peerhub/dispatch/contract.py:182` (`_require_nonnegative_int`)
