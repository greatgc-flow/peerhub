# Domain-Oracle Verifier — Design Reconciliation R1

**Status:** `cc` judgment record reconciling two independent drafts
(`ag.deepthink`, `cx.deepthink`) into `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md`.
Documentation-only; carries no implementation or ratification authority by
itself.

## Where ag and cx converged

- A post-lifecycle, pre-finalization verification step, separate from
  `runner.py`'s tested lifecycle engine, gates a new `SPEC_FAITHFUL`
  status rather than replacing `V1_CAPTURE`.
- Event scripts supply raw facts only; pre-computed outcome fields are
  rejected before verification runs.
- Structure: several small, narrowly-scoped domain modules behind one
  shared, mechanical oracle interface/comparison contract — not one
  monolithic rule engine, and not four fully independent runners.
- A static, checked-in fixture-to-oracle registry, never a script-supplied
  import path or expression.

## Where they disagreed, and the resolution

1. **Domain grouping.** `ag` grouped `DT-02..05` under "RT (Routing &
   Pacing)" alongside `RT-04..06`, and applied the quota-consumption
   pacing formula from the earlier, unrelated
   `QUOTA-PERIOD-SCALING-POLICY-R1.md` round (target-fraction ramp against
   a compressed effective period) to derive its invariant. `cx` treated
   `DT-02..05` as a separate **transport** concern (chunk framing,
   independent timeout classification, cancellation ordering, tree
   closure) with no relation to routing or quota pacing at all.
   **Resolution: cx.** `DT-02..05`'s own `.notes.md` files (written by
   `cx` itself in the prior round) describe line-normalization, dual-
   terminal-classification, missing cancel semantics, and tree-identity
   uncertainty — none of these are about candidate weighting or quota
   consumption. Reusing the quota-pacing formula here is a real mixup, not
   a defensible alternative reading; `ag`'s RT-family write-up is not
   used.
2. **Broker atomicity.** `ag`'s `DomainOracle` interface was purely
   computational (`compute_expected`/`evaluate` over claimed state); it
   did not address how `GB-01`'s CAS-atomicity claim (revision + receipt +
   outbox row are all-or-nothing) could be verified when no real database
   exists to observe. `cx` split the contract into a pure `DomainOracle`
   (prediction) and a `DomainSubjectAdapter` (execution), with `GB-01`
   alone permitted an isolated, fresh-root SQLite instance so atomicity is
   actually exercised, not just asserted. **Resolution: cx.** A pure
   comparison cannot prove transaction atomicity; something has to run the
   transaction under fault injection.
3. **Anti-circularity depth.** `ag` proposed one concrete control (reject
   scripts carrying pre-computed outcome keys — `ORACLE_INPUT_TAINTED`).
   `cx` proposed a fuller set: oracle/adapter modules for one fixture may
   not import each other or share decision helpers; every fixture needs at
   least one required-to-fail negative vector; boundary/metamorphic vector
   pairs per family; independent authorship/review; fail-closed on
   unknown oracle ID, missing artifact, or digest mismatch; and an
   explicit statement that reference-adapter success is acceptance-harness
   evidence, not production evidence. **Resolution: cx's fuller set,
   with ag's taint-rejection check kept as an additional concrete,
   cheap safeguard** — the two are complementary, not conflicting.
4. **New sub-blocker `cx` surfaced that `ag` did not:** `RT-05`'s seed-
   serialization and candidate-selection algorithm is not frozen anywhere
   in the existing corpus ("deterministic" without a formula). `cx`
   proposed a concrete default (SHA-256 of the canonical `{request_id,
   snapshot_digest}` pair, modulo the sorted eligible-candidate count).
   **Adopted as a proposal in `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` §6,
   flagged as its own explicit ratification item** rather than silently
   folded into the general gate, since it is a genuinely new frozen
   behavior, not a restatement of an existing one.

## Assessment

`ag`'s taint-check mechanism and its "narrow adapters behind one shared
interface" structural call were sound and retained. Every other
substantive disagreement resolved in `cx`'s favor, and two of them
(points 1-2) are concrete correctness gaps in `ag`'s draft — a wrong
domain grouping using a formula that doesn't apply, and a verification
model that cannot actually prove the one claim (CAS atomicity) it was
supposed to check — not matters of preference.

## Final Call

Sent to both `ag` and `cx` before commit, per the R:10 consensus protocol
(`protocol.json.consensus.r10_voters = [cc, ag, cx]`), including the
DT/RT mixup and the GB-01 atomicity gap so `ag` has a concrete chance to
object if this reconciliation misread its draft.
