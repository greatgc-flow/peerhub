# Unified User Settings Surface R1

**Status:** Proposed Phase 0 design addition — design-only. Authorizes no
implementation, database creation, or live configuration mutation.

**Scope:** One typed CRUD + observation interface over user-tunable
settings, including `QuotaPacingRule` (see
`QUOTA-PERIOD-SCALING-POLICY-R1.md`) and future user-defined knobs.
"Unified" means one interface surface, not one database table and not a new
canonical owner for every setting.

## 1. Setting catalog and canonical owners

A declarative `SettingDescriptor` registry is the catalog, not the store:

```text
SettingDescriptor
  setting_key
  schema_id
  canonical_owner        # aggregate root only, e.g. PeerInstanceConfig, PeerProfileBinding, RoutingPolicy
  owner_subpath           # optional, for a value object owned by canonical_owner, e.g. RoutingPolicy.QuotaPacingRule[pool_id]
  allowed_scopes
  writable / read_only
  sensitivity / redaction
  default_or_required
  authorization_class
```

Each descriptor routes reads/writes to the record already owned by its
canonical component. The façade stores no duplicate setting values.
`canonical_owner` names an aggregate root only. `QuotaPacingRule` is not a
peer canonical owner: it is a typed subresource of `RoutingPolicy`
(`QUOTA-PERIOD-SCALING-POLICY-R1.md` §1), so a pacing setting's descriptor
sets `canonical_owner = RoutingPolicy` and `owner_subpath =
QuotaPacingRule[quota_pool_id]`. A generic key/value override table for
arbitrary settings is explicitly rejected (§5) — it would recreate exactly
the "second independently-mutable copy of a config fact" failure this
project has already hit once (the peer/model pin drift noted in
`ARCHITECTURE.md`).

## 2. Command contract

Namespace: `configuration.*` — a new PeerHub command surface, **not** an
addition to the frozen legacy 90-action inventory
(`ACTION-INVENTORY-RECEIPT-R1.md` binds that vector's exact hash; extending
it is out of scope here and would require its own additive, ratified
change). Exposed identically through PeerHub's application API, embedded
client, CLI, and JSONL protocol.

| Operation | Contract |
|---|---|
| `configuration.setting.list` | List descriptors + setting references for an authorized scope. |
| `configuration.setting.read` | Return configured value, origin, owner revision, and current effective view. |
| `configuration.setting.write` | Explicit `CREATE`, `UPDATE`, or `DELETE` only — no ambiguous upsert. Requires typed value, expected revision (CAS), idempotency key, actor, and reason. The CAS revision is always the *aggregate root's* revision (e.g. `RoutingPolicy.revision`), never a subresource's own counter, so a subresource never becomes a second independent authority. |
| `configuration.setting.observe` | Return a derived snapshot + outbox cursor + changes since a prior cursor. Read-only; never triggers a provider probe, refresh, or repair. |

## 3. Effective-value monitoring

```text
SettingObservation
  setting_ref + configured_revision
  configured_value + origin
  effective_value/state + evaluated_at
  evidence_refs
  policy/decision/effect_refs
  outbox_position
```

For a pacing setting, "effective" includes computed target time, planned
fraction, measured fraction, deviation, evidence freshness, and recent
`RouteDecision` references — derived on read (or from immutable events),
never persisted as another writable quota/config copy. Observation must
correlate an effect with a decision that explicitly references the
setting's revision; temporal correlation with telemetry alone is not proof
of causation.

## 4. Defaults, deletion, and scope

Deleting a setting exposes a versioned built-in default, or is forbidden
for a required setting — it must never fall through to an independently
mutable JSON/environment value that the façade doesn't track. V1 supports
one logical interface per `PeerHubHome`; a genuinely user-global setting
spanning multiple homes needs a separately justified account-scoped
canonical coordinator. Copying the same mutable setting into every
workspace database is rejected as a repeat of the SSOT failure in §1.

## 5. Safety and coupling constraints

- Every route/dispatch freezes the policy revision relevant to it; a
  setting change before the effect lands triggers re-plan or
  `CONFIGURATION_STALE`, never a silent mid-flight change.
- Constitutional/protocol-level policy, measured evidence, credentials, and
  authority state are never exposed as an ordinary writable user setting
  through this surface.
- `configuration.setting.observe` performs no side effect of any kind;
  refresh/repair remain separate, explicitly authorized commands.
- A generic settings table bypassing per-domain validation (the
  "configuration god-object" failure mode) is rejected; every descriptor
  must resolve to one existing canonical owner or a newly, separately
  ratified one.

## 6. Acceptance and ratification

Required fixtures: CRUD round-trip with CAS conflict; idempotent write
retry; redaction of sensitive settings in list/read; owner-routing
correctness (no duplicate storage); observe cursor recovery after gap;
`CONFIGURATION_STALE` on in-flight drift; deletion-to-default behavior for
both required and optional settings. Ratification of this document is
documentation-only and grants no implementation or mutation authority,
consistent with `TDD-READINESS-GATE-R1.md`.

## 7. Provenance

Drafted independently by `ag.deepthink` and `cx.deepthink` from a shared
brief (2026-07-29), reconciled in
`QUOTA-SETTINGS-DESIGN-RECONCILIATION-R1.md`. The command-namespace and
storage-model resolutions in that record both favor the `cx` draft over the
`ag` draft, for reasons stated there.
