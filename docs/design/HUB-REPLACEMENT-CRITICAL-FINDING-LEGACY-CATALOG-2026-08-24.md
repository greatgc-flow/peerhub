# CRITICAL FINDING: `LEGACY_CATALOG` already maps ~90 of hub.py's actions to native target names (2026-08-24)

Status: terminal-authored, direct read of `peerhub/application/legacy.py`.
**This is the single most consequential finding of the entire design-
reinforcement effort — it substantially answers gap-1's "define the
native command surface" question for ALL 7 categories at once**, and
should be read before trusting any gap doc's own "native command surface"
section, several of which invented CLI-style names that should now defer
to this real dotted-path convention instead.

## What exists, exactly

`peerhub/application/legacy.py` has:

- `LegacyActionCall{action, arguments}` — the parsed legacy invocation.
- `LEGACY_CATALOG: dict[str, str]` — **maps ~90 of hub.py's real legacy
  action names to a proposed native "target_method" dotted path.** This
  is essentially the complete legacy-action inventory gap-1 named as
  priority #2 ("caller discovery and migration inventory") — already
  done, at least for the ACTION side (not yet the CALLER side — this
  catalog doesn't tell you which scripts/docs/tests invoke each action,
  only that the action exists and where it should map to).
- `LegacyTranslator.translate(call, submission) -> LegacyTranslationOutcome`
  — for each action: if not in `LEGACY_CATALOG` → `UnknownLegacyAction`;
  if in the catalog but no real handler exists yet → `KnownLegacyActionNotBacked
  {legacy_action, target_method, ledger_status="INVENTORIED", reason="no
  PeerHub handler with semantic backing"}`; if a real handler exists →
  `TranslatedCommand(command=<real Command instance>)`.
- **Only 3 of ~90 catalog entries currently have real handlers**: `ask` →
  `SubmitDispatch`, `ask-all` → `SubmitManyDispatch`, `ask-coordinator` →
  `SubmitCoordinatorDispatch`. Everything else in the catalog is
  `INVENTORIED` but `NOT BACKED` — the mapping target is decided, the
  implementation doesn't exist yet.

## The full catalog (verbatim, `peerhub/application/legacy.py`)

```python
LEGACY_CATALOG = {
    'init-session': 'coordination.session.open',
    'end-session': 'coordination.session.close',
    'send': 'coordination.message.send',
    'broadcast': 'coordination.message.broadcast',
    'mark-read': 'coordination.message.mark_read',
    'append-log': 'governance.audit.append',
    'archive-file': 'governance.artifact.archive',
    'update-status': 'coordination.mission.update',
    'check': 'coordination.message.check',
    'status': 'peerhub.status.read',
    'check-gate': 'health.admission.check',
    'ask': 'dispatch.submit',                          # BACKED (SubmitDispatch)
    'ask-all': 'dispatch.submit_many',                  # BACKED (SubmitManyDispatch)
    'ask-coordinator': 'dispatch.submit_coordinator',   # BACKED (SubmitCoordinatorDispatch)
    'consensus-propose': 'consensus.round.propose',
    'consensus-vote': 'consensus.vote.cast',
    'consensus-check': 'consensus.round.read',
    'consensus-sweep': 'consensus.round.sweep',
    'register-node': 'configuration.instance.register',
    'list-nodes': 'configuration.instance.list',
    'health-update': 'health.evidence.record',
    'health-check': 'health.projection.read',
    'peer-status': 'health.instance.status',
    'context-fill': 'coordination.context.fill',
    'checkpoint': 'coordination.checkpoint.create',
    'peer-quarantine': 'health.admission.quarantine',
    'peer-recover': 'health.recovery.authorize_probe',
    'new-topic': 'coordination.topic.create',
    'clear-room': 'coordination.room.clear',
    'preflight': 'peerhub.preflight',
    'context-hash': 'peerhub.context.hash',
    'report-error': 'telemetry.error.record',
    'feedback-add': 'governance.feedback.create',
    'feedback-list': 'governance.feedback.list',
    'feedback-resolve': 'governance.feedback.resolve',
    'artifact-claim': 'governance.artifact.claim',
    'artifact-status': 'governance.artifact.status',
    'artifact-finalize': 'governance.artifact.finalize',
    'leader-yield': 'routing.leadership.yield',
    'leader-claim': 'routing.leadership.claim',
    'elect-leader': 'routing.leadership.elect',
    'discover': 'routing.candidate.discover',
    'assign-role': 'coordination.role.assign',
    'release-role': 'coordination.role.release',
    'role-status': 'coordination.role.status',
    'health-precheck': 'health.admission.precheck',
    'health-sweep': 'health.projection.sweep',
    'freshness-sweep': 'telemetry.freshness.sweep',
    'terminal-handoff': 'coordination.terminal.handoff',
    'terminal-duty-sweep': 'coordination.terminal.duty_sweep',
    'terminal-heartbeat': 'coordination.terminal.heartbeat',
    'terminal-close': 'coordination.terminal.close',
    'append-handoff': 'coordination.handoff.append',
    'task-checkpoint': 'coordination.task.checkpoint',
    'task-status': 'coordination.task.status',
    'task-failover': 'coordination.task.failover',
    'approval-request': 'governance.approval.request',
    'file-lock': 'governance.lock.acquire',
    'file-unlock': 'governance.lock.release',
    'lock-status': 'governance.lock.status',
    'profile-validate': 'configuration.profile.validate',
    'lease-status': 'dispatch.lease.status',
    'lease-sweep': 'dispatch.lease.sweep',
    'model-status': 'configuration.model.status',
    'transient-scan': 'telemetry.transient.scan',
    'directive-add': 'host.directive.add',
    'directive-list': 'host.directive.list',
    'directive-clear': 'host.directive.clear',
    'lessons-list': 'governance.lesson.list',
    'lessons-propose': 'governance.lesson.propose',
    'lessons-activate': 'governance.lesson.activate',
    'lessons-retire': 'governance.lesson.retire',
    'lesson-broadcast': 'coordination.lesson.broadcast',
    'lesson-sweep': 'governance.lesson.sweep',
    'lesson-inject': 'host.lesson.inject',
    'thread-new': 'coordination.thread.create',
    'thread-append': 'coordination.thread.append',
    'thread-react': 'coordination.thread.react',
    'thread-promote': 'coordination.thread.promote',
    'alert-raise': 'coordination.alert.raise',
    'proposal-add': 'governance.proposal.create',
    'proposal-vote': 'governance.proposal.vote',
    'proposal-list': 'governance.proposal.list',
    'broker-submit': 'governance.mutation.submit',
    'broker-drain': 'governance.effect.drain',
    'broker-status': 'governance.mutation.status',
    'update-signatures': 'peerhub.signature.update',
    'arbiter-review': 'consensus.arbiter.review',
    'credit-status': 'host.credit.status',
    'credit-consume': 'host.credit.consume',
}
```

## Namespace convention (inferred from the catalog itself)

`coordination.*` (session/message/mission/context/checkpoint/topic/room/
terminal/handoff/task/role/thread/lesson-broadcast/alert-raise),
`governance.*` (audit/artifact/feedback/lock/lesson-most/proposal/
mutation/effect/approval), `health.*` (admission/evidence/projection/
instance/recovery), `dispatch.*` (submit/lease), `consensus.*`
(round/vote/arbiter), `routing.*` (leadership/candidate),
`configuration.*` (instance/profile/model), `telemetry.*`
(error/freshness/transient), `host.*` (directive/lesson-inject/credit —
appears to mean "the local machine/session," distinct from `governance.*`
governance-artifact operations), `peerhub.*` (status/preflight/
context-hash/signature — meta/self operations).

## What this changes about gaps 1-7

**This resolves or substantially narrows a large fraction of the open
questions across every gap doc.** Specifically:

- **Gap-1**: "exact initial compatibility command set" and "native
  command/API surface" questions are LARGELY answered — the full mapping
  exists, just needs implementing. Gap-1's proposed native surface
  (`peerhub dispatch/session/consensus/health/status/migrate`) should be
  revised to align with THIS real dotted-path convention instead — e.g.
  not `peerhub session open` but a command backing `coordination.session.open`.
- **Gap-2**: `consensus.round.propose/read/sweep` + `consensus.vote.cast`
  + `consensus.arbiter.review` are the REAL target names — gap-2's
  proposed `peerhub consensus propose/vote/status/sweep/escalate` should
  be reconciled against these (note: no `consensus.round.escalate` in the
  catalog — gap-2's "escalate" concept may need to be `consensus.round.*`
  with an escalation payload, or map to `governance.approval.request`
  instead; needs a dedicated check).
- **Gap-3**: `coordination.session.*`, `coordination.room.clear`,
  `coordination.topic.create` (= `new-topic`), `coordination.thread.*`,
  `coordination.terminal.*`, `coordination.handoff.append` are ALL real
  target names. **This DIRECTLY CONTRADICTS the prior reconciliation
  round's conclusion that "room/thread is a genuine gap not found
  anywhere in peerhub"** — it turns out there IS a planned namespace
  (`coordination.room.*`, `coordination.thread.*`), it's just
  `INVENTORIED`/`NOT BACKED` like everything else outside the 3 backed
  actions. The gap is real at the IMPLEMENTATION level, not the naming/
  design level — gap-3's own room/thread data-model proposal is probably
  still needed as the design FOR these target methods, since the catalog
  only gives a name, not a schema.
- **Gap-4**: `health.admission.check/quarantine/precheck`,
  `health.evidence.record`, `health.projection.read/sweep`,
  `health.recovery.authorize_probe`, `health.instance.status`,
  `routing.leadership.yield/claim/elect`, `routing.candidate.discover`,
  `coordination.role.*` are the real target names — very close to gap-4's
  own reconciled design; `routing.*` for leadership/discovery is a naming
  correction (gap-4's reconciliation guessed these might be under health,
  the catalog says `routing.*`).
- **Gap-5**: `coordination.task.checkpoint/status/failover` and
  `governance.approval.request` are the real target names — **directly
  confirms the earlier reconciliation's inference that `approval-request`
  is a `governance.*` concept, NOT `AdmissionCoordinator`'s `health.*`
  admission** (independent confirmation from two different angles).
- **Gap-6**: `governance.feedback.*`, `governance.lesson.*` (mostly, with
  `coordination.lesson.broadcast` and `host.lesson.inject` as two notable
  exceptions living in different namespaces than the other lesson
  actions), `governance.proposal.*`, `governance.mutation.submit/status`
  + `governance.effect.drain` (= `broker-submit`/`broker-status`/
  `broker-drain`, directly confirming the governance-broker connection
  gap-2/gap-6's reconciliation already inferred), `host.directive.*`,
  `coordination.alert.raise`, `consensus.arbiter.review` — this
  significantly corrects and completes gap-6's native surface.
- **Gap-7**: `host.credit.status/consume`, `configuration.model.status`,
  `telemetry.*` (error/freshness/transient) — real target names, matches
  gap-7's proposed `credit-status`/`credit-consume`/`model-status`
  mapping almost exactly.

## What this does NOT resolve

- The catalog gives a NAME and rough NAMESPACE, not a schema, payload
  shape, or state machine — every gap's own detailed design (data model,
  state transitions, invariants) is still real, needed work.
- **Checked**: `ledger_status` is a plain `str` field (not an enum), and
  `"INVENTORIED"` is the ONLY literal value found anywhere in real code —
  there is no formal multi-state migration ledger yet (no
  `COMPATIBLE`/`MIGRATION_REQUIRED`/etc. equivalents exist today). Gap-1's
  proposed per-command state machine (`COMPATIBLE`/`COMPATIBLE_WITH_LIMITS`/
  `MIGRATION_REQUIRED`/`UNSUPPORTED`) is therefore still real, needed
  design work — `ledger_status` is a plausible field to extend with those
  values, not something that already has them.
- The CALLER side of gap-1's migration inventory (which scripts/docs/
  tests invoke each action) is still undone — this catalog only proves
  the ACTION exists and its target name, not who calls it today.
- Argument/payload translation is only shown for the 3 backed actions
  (`prompt` extraction) — the other ~87 actions' argument shapes aren't
  specified here.

## Immediate next step

Every gap doc's "native command surface" section should be revised to
cite `LEGACY_CATALOG`'s real target_method names as the AUTHORITATIVE
naming convention, with each gap's own proposed CLI-style names (e.g.
`peerhub consensus propose`) treated as a human-facing CLI verb that maps
onto the real dotted target method, not a competing naming scheme.
