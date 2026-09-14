# Quota Pacing & Unified Settings — Design Reconciliation R1

**Status:** `cc` judgment record reconciling two independent Round-1 drafts
(`ag.deepthink`, `cx.deepthink`) into `QUOTA-PERIOD-SCALING-POLICY-R1.md`
and `UNIFIED-SETTINGS-SURFACE-R1.md`. Documentation-only; carries no
implementation or ratification authority by itself.

## Where ag and cx converged

- `UsageProvider` stays strictly read-only/policy-blind; pacing is a
  separate pure evaluator combining measured evidence with policy config.
- Same pacing math: a compressed `effective_period` (~6.25 days against a
  ~7-day/168h window) drives a linear `target_fraction` ramp — terminal
  100%, non-terminal 80%.
- `pool_role` must be explicit configuration, never inferred from peer
  identity.
- The settings surface is a façade with list/read/write/observe
  operations, CAS-guarded writes, and a read-only, non-probing observe.

## Where they disagreed, and the resolution

1. **Command namespace.** `ag` proposed extending the existing
   `config.*`/90-action surface. `cx` argued for a new `configuration.*`
   façade, explicitly *not* appended to the legacy 90-action vector, since
   `ACTION-INVENTORY-RECEIPT-R1.md` (committed this session) freezes that
   vector's exact hash (`2065c0b6...`) as a measured baseline. **Resolution:
   cx.** Appending to a hash-bound frozen inventory outside its own governed
   change process would invalidate the freeze for no architectural benefit;
   a new command surface is free to grow under its own ratification.
2. **Settings storage model.** `ag` proposed a single generic
   `user_config_overrides(setting_key, value_json, ...)` SQLite table as
   the one persistent store. `cx` proposed a `SettingDescriptor` catalog
   that routes reads/writes through to each setting's existing canonical
   owner (`PeerInstanceConfig`, `RoutingPolicy`, the new `QuotaPacingRule`,
   etc.), storing no value itself. **Resolution: cx.** A generic KV
   override table is, for any setting that already has a canonical owner
   (e.g. a pool's `target_fraction`), exactly the "second
   independently-mutable copy of a config fact" this project's own
   architecture already names as a real, previously-hit failure mode. cx's
   own risk list ("configuration god-object") makes this explicit; ag's
   draft doesn't reconcile its storage proposal against that same risk it
   also implicitly created.
3. **Safety valve.** Only `ag` proposed a concrete
   `TERMINAL_UNESCAPE_SAFETY_VALVE` for the case where all pools in a
   family are simultaneously throttled by the compressed period. cx's
   draft implies the same need ("manual dispatch... must be auditable,
   cannot silently masquerade as compliance") but did not specify a
   mechanism. **Resolution: kept, folded into cx's structure** — added to
   `QUOTA-PERIOD-SCALING-POLICY-R1.md` §3 as a bounded, terminal-only,
   explicitly logged exception; it does not relax the 80%/100% targets,
   only prevents an unconditional blackout.
4. **Document split.** `ag` proposed one combined document; `cx` proposed
   two, since pacing semantics and settings-surface authority have
   different evidence/review gates. **Resolution: cx** — matches this
   corpus's existing convention of narrowly-scoped R1 documents.

## Final Call

Sent back to both `ag` and `cx` as a Final Call ACK request before commit,
per the R:10 consensus protocol (`protocol.json.consensus.r10_voters = [cc,
ag, cx]`).

- **`ag.deepthink`: ACK.** No objection to points 1-4. (Its reply also cited
  a receipt hash, `1c3054f5...`, that does not match the real
  `ACTION-INVENTORY-RECEIPT-R1.md` digest, `2065c0b6...`; treated as an
  unverified/incorrect citation and not propagated into either document —
  consistent with this project's standing rule to verify peer citations
  before trusting them.)
- **`cx.deepthink`: ACK-WITH-CONCERN.** Flagged that `UNIFIED-SETTINGS-SURFACE-R1.md`
  §1 listed `QuotaPacingRule` as a peer `canonical_owner` alongside
  `RoutingPolicy`, contradicting `QUOTA-PERIOD-SCALING-POLICY-R1.md` §1's
  own statement that `QuotaPacingRule` is owned *by* `RoutingPolicy` — and
  that a separate `revision` field on `QuotaPacingRule` would function as
  an implicit second CAS authority. **Concrete and correct; fixed in both
  documents**: `canonical_owner` now names aggregate roots only,
  `QuotaPacingRule` is addressed via a `SettingDescriptor.owner_subpath`,
  its standalone `revision` field was removed, and all writes CAS against
  `RoutingPolicy.revision`.

Both peers are unanimous on the resolution as corrected. Neither original
draft is discarded — both remain in IPC session history as independent
design evidence; this record is the additive synthesis, not a rewrite of
either.
