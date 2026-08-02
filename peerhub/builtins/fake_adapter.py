"""Fake reference adapter for Slice 5 TDD.

This module contains importable Step 2 contract shapes only, matching the
discipline established by ``peerhub.dispatch.process`` (Step 2): a real,
structurally-complete ``PeerAdapter`` with a populated ``descriptor``, but
method bodies raise ``NotImplementedError`` until their scheduled step.
``interpret_chunk``/``finalize_decoded_output`` (the incremental decode
path DT-02 exercises) and the four ``PeerAdapter`` protocol methods are
Step 3 work (SLICE5-KICKOFF-R1.md "Reducer set": "peerhub/builtins/
fake_adapter.py: interpret_output (pure translation...)").
"""

from __future__ import annotations

from collections.abc import Sequence

from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    DecodedOutput,
    DecoderEvent,
    InvocationPlan,
    OutputChannel,
    OutputDecoder,
    PeerDescriptor,
    ProfileDescriptor,
    ProtocolAssessment,
    PromptPolicy,
    SessionHint,
)
from peerhub.core.execution import (
    ProcessTerminalEvidence,
    TransportKind,
    TransportLimits,
)

_FAKE_PROFILE = ProfileDescriptor(
    profile_id="fake-standard",
    profile_class="tier",
    supports_reasoning_effort=False,
)

_FAKE_DESCRIPTOR = PeerDescriptor(
    adapter_id="fake-peer",
    adapter_version="0.0.0-slice5",
    peer_kind="fake",
    profiles=(_FAKE_PROFILE,),
    transports=frozenset({TransportKind.PIPE, TransportKind.PTY}),
    # The fake descriptor deliberately does not declare GRACEFUL_CANCEL
    # (SLICE5-KICKOFF-R1.md item 4): vendor-specific graceful-cancel
    # recipes remain deferred past this slice. SESSION is likewise not
    # declared: SessionHint's own docstring says this fake rejects every
    # non-null session hint (no real session support), and ARCHITECTURE.md
    # is explicit that "a descriptor declaring a capability it doesn't
    # actually implement is a load-time error, not a runtime surprise" --
    # cross-review finding, cx, 2026-08-02 (this file had declared SESSION
    # while contract.py's own SessionHint docstring said the opposite).
    capabilities=frozenset({Capability.STREAM}),
    usage_provider_id=None,
    readiness_probe_id="fake-peer-readiness",
)


class FakePeerAdapter:
    """Deterministic, structurally-complete ``PeerAdapter`` for TDD.

    ``descriptor`` is real and populated so this genuinely satisfies the
    ``PeerAdapter`` Protocol shape; the parsing/planning method bodies are
    Step 3 (this class exists in Step 2 to unblock the DT-02 import and to
    formally declare the interface, not to implement it yet).
    """

    descriptor: PeerDescriptor = _FAKE_DESCRIPTOR

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        raise NotImplementedError("implemented in Slice 5 Step 3")

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan:
        raise NotImplementedError("implemented in Slice 5 Step 3")

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
        raise NotImplementedError("implemented in Slice 5 Step 3")

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        raise NotImplementedError("implemented in Slice 5 Step 3")

    # --- Incremental decode convenience path exercised by DT-02 ----------
    #
    # This mirrors OutputDecoder.feed()/finalize() but is invoked directly
    # on the adapter rather than through new_decoder() -- DT-02 tests the
    # fake peer's own framing/decoding behavior in isolation, not the
    # PeerAdapter-to-OutputDecoder wiring (that wiring is exercised later,
    # by the Step 6 vertical-dispatch integration test).

    def interpret_chunk(
        self, chunk: bytes, *, channel: OutputChannel = OutputChannel.STDOUT
    ) -> tuple[DecoderEvent, ...]:
        raise NotImplementedError("implemented in Slice 5 Step 3")

    def finalize_decoded_output(self) -> DecodedOutput:
        raise NotImplementedError("implemented in Slice 5 Step 3")
