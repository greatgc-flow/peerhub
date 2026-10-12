# GOVERNANCE-DISPATCH-R2-cross-review-ag-deepthink-2026-09-24

**Author**: ag.deepthink
**Role**: Round 2 Cross-Reviewer

## 1. Concrete Mechanisms Comparison (6 Gaps + IPC Hygiene)

### Gap 1: `collab_rate` (Consultation Depth)
- **ag.opus**: Proposes a 5-value `ConsultationDepth` Enum (`NONE`, `NOTIFY`, `REVIEW`, `QUORUM`, `UNANIMOUS`) mapped bijectively to the old 0-10 scale.
- **cx.astra**: Proposes a 5-state hierarchy in D03.1 (None, Notify, Review, Quorum, Unanimous).
- **Analysis**: Both proposals converged on the exact same 5-state abstraction, proving the legacy 0-10 scale was false precision.
- **Verdict**: Use the 5-state Enum. It maximizes **(a) simplicity** while maintaining **(b) completeness**.

### Gap 2: Final Call
- **ag.opus**: Makes Final Call configurable (`final_call_rule`) in `dispatch-policy.toml`.
- **cx.astra**: Formalizes Final Call as a strict integrity barrier (D04.1) mandatory for Tier-0/high-risk tasks, capturing exactly who ACKs.
- **Analysis**: cx.astra handles the edge cases (e.g. concurrent retraction vs authorization) much more rigorously (b), but ag.opus's configuration-driven approach (a) fits peerhub's existing config model better (c).
- **Verdict**: Adopt ag.opus's configuration trigger, but back it with cx.astra's rigorous CAS-backed ACK state machine.

### Gap 3: Quarantine from Review
- **ag.opus**: Uses a `health_consequence` policy that links an `ESCALATE` review outcome directly to `health.open_manual_quarantine()`.
- **cx.astra**: Identifies this tension in D15.1 O1, recommending preserving containment without fabricating evidence.
- **Analysis**: ag.opus provides the concrete mechanical fix that perfectly answers cx.astra's philosophical concern. It touches minimal code (c).
- **Verdict**: Adopt ag.opus's `health_consequence` mapping.

### Gap 4: Session Rotation
- **ag.opus**: Exposes `--session-policy` explicitly and wires it to the existing `SessionRotationSaga.evaluate_and_claim()`.
- **cx.astra**: Subsumes this into a broader "prepare intent / plan execution" phase.
- **Analysis**: ag.opus's approach is far simpler (a) and directly reuses unwired existing code (c).
- **Verdict**: Adopt ag.opus's direct saga wiring.

### Gap 5: Profile Routing (Effort)
- **ag.opus**: Wires `--effort-hint` as a soft advisory preference that doesn't block dispatch.
- **cx.astra**: Flags this in D15.1 O2 as a "routing-before-evidence" tension.
- **Analysis**: Both agree that automatic routing cannot be strictly enforced without a real evidence source. ag.opus's "advisory only" output is the most honest and simple approach (a, b).
- **Verdict**: Adopt ag.opus's advisory routing.

### Gap 6: Telemetry
- **ag.opus**: Proposes a pre-dispatch `refresh_before_dispatch` policy hook.
- **cx.astra**: Categorizes it under diagnostic views.
- **Analysis**: A pre-dispatch poll is the simplest way to emulate legacy's synchronous check without rewriting the poller.
- **Verdict**: Adopt ag.opus's pre-dispatch hook.

### IPC Hygiene (`--query-file`)
- **ag.opus**: Adds `--query-file` to CLI, maps directly to `stage_prompt()` for oversized payloads.
- **cx.astra**: Treats this as part of a grander payload/intent lifecycle.
- **Analysis**: ag.opus's approach is radically simpler (a) and directly utilizes the `prompt_transport.py` staging built on 2026-09-09 (c).
- **Verdict**: Adopt ag.opus's direct wiring.

---

## 2. Addressing cx.astra's Flagged Uncertainties (D15)

- **O1 (Quarantine-interpretation tension)**: cx.astra recommends preserving containment/operator authority while rejecting evidence fabrication. I **agree**. ag.opus's proposal already solves this elegantly: the review outcome explicitly triggers `open_manual_quarantine()` rather than spoofing a health check. This is the correct synthesis.
- **O2 (Routing-before-evidence tension)**: cx.astra asks if automatic routing can ship as a declared preference before real quality evidence exists. I **agree** with cx.astra's recommendation (Yes, with truthful labels). ag.opus's "advisory only" routing does exactly this.
- **O3 (Host-authority-boundary question)**: cx.astra highlights that named operator grants must be isolated from peer impersonation. I **agree**. This cannot be solved by architecture prose alone; it requires a real OS-level or cryptographic boundary. This remains an open requirement for deployment, but does not block the dispatch logic itself.

---

## 3. Addressing ag.opus's Flagged Uncertainties (Section 9)

- **9.1 (Enum vs. Numeric Scale for ConsultationDepth)**: Use the **Enum**. Hub.py's 0-10 scale was a false precision that clustered into 5 distinct modes anyway. The enum strictly bounds the state space, perfectly serving the simplicity requirement.
- **9.2 (Separate Orchestrator vs. Integrated into ApplicationAPI.submit)**: Use a **Separate Orchestrator** (`DispatchOrchestrator`). Integrating it into `ApplicationAPI.submit()` would bloat an already massive class. A thin orchestrator that reads a frozen policy and calls services maintains clear separation of concerns (c).
- **9.3 (Telemetry bug status)**: As confirmed by the terminal, the specific crash bug in `quota_polling.py` is fixed, but `rate_limitsByLimitId` (per-limit-family `rate_limit_state`) tracking was never built. Thus, `presenter.py` still lacks `rate_limit_state`. The user-visible gap persists. The telemetry policy hook should be built, but full telemetry parity remains blocked by the upstream normalizer defect.

---

## 4. Assessment of cx.astra's D13 Capability-Superset Ledger

cx.astra's D13 inventory of every legacy `action_*` function is an impressive demonstration of MECE completeness, but it is **scope creep**. 

**Honest Assessment**: 
The user's central requirement is **radical simplicity**. Importing the entire legacy surface area into the new formal model runs directly counter to this. Many legacy actions were host-level diagnostics, dead ends, or redundant wrappers that peerhub explicitly redesigned away from. Forcing them into the new Intent/Contract/Attestation paradigm adds massive architectural weight for zero functional gain. 

We must confine this round strictly to the **6 known gaps + IPC hygiene**. If a legacy action falls outside those, it should be evaluated individually later, not bulk-imported into the dispatch architecture.

---

## 5. Converged Direction

I propose a **synthesis heavily weighted toward ag.opus's structural simplicity, but borrowing cx.astra's rigor for consensus state**.

**What survives into Round 3:**
1. **From ag.opus**: The `DispatchPolicy` (frozen dataclass) and `DispatchOrchestrator` concept. It is the clearest, most direct way to resolve the 6 gaps without tearing up the existing `ApplicationAPI`. 
2. **From ag.opus**: The TOML configuration layer (`dispatch-policy.toml`).
3. **From ag.opus**: The 5-value `ConsultationDepth` Enum.
4. **From cx.astra**: The rigorous state machine definitions for Final Call and Quorum (D03/D04). ag.opus leaves the "how" of Final Call a bit loose; cx.astra's exact CAS-backed transition model (where an ACK is a distinct, retractable state) is required for correctness.

**What is dropped:**
1. **cx.astra's D13 Ledger**: Excluded for scope creep.
2. **cx.astra's 20+ policy namespaces**: Dropped in favor of ag.opus's single, cohesive `DispatchPolicy` object.
3. **Numeric collab_rate**: Dropped entirely.

**Why this isn't just a union**: We are actively rejecting cx.astra's broader dispatch framework and lifecycle states (D05) because ag.opus's thin orchestrator requires touching far less existing code to achieve the exact same capability.

---

## 6. Unresolved Disagreements

There are no architectural disagreements blocking Round 3 drafting. The convergence on the 5-state consultation depth and the resolution of the quarantine tension via manual review triggers unify the two proposals' biggest theoretical divides.

The only remaining open question is **operational**: O3 (Host-authority-boundary). How do we cryptographically or OS-isolate the trusted human grant channel from a compromised peer? This cannot be answered in a dispatch design document and must be flagged for the deployment/security phase.
