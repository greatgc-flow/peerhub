"""Fake reference adapter for Slice 5 TDD.

Step 3 (this session, 2026-08-02) implements exactly the piece that has a
concrete, testable oracle: ``interpret_chunk``/``finalize_decoded_output``,
against DT-02's exact byte-in/lines-out expectations. ``prompt_policy``,
``plan_invocation``, ``new_decoder``, and ``interpret_output`` remain
``NotImplementedError`` stubs -- no test exercises them yet, and neither
ARCHITECTURE.md nor SLICE5-KICKOFF-R1.md gives concrete parsing rules for
the fake peer's *protocol* framing (CHUNK/EXIT/SPAWNED event vocabulary)
the way DT-02's test gives concrete rules for raw-byte/CRLF/UTF-8 framing.
Implementing those now would be inventing an unratified shape, the exact
failure mode this project's discipline stops for (see Step 2's identical
stop on the adapter boundary before this session's ratification round).
"""

from __future__ import annotations

import codecs
from collections.abc import Sequence

from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    DecodedOutput,
    DecoderEvent,
    DecoderEventKind,
    InvocationPlan,
    OutputChannel,
    OutputDecoder,
    PeerDescriptor,
    ProfileDescriptor,
    ProtocolAssessment,
    PromptPolicy,
    SessionHint,
)
from peerhub.core.errors import ErrorCode
from peerhub.core.execution import (
    ProcessTerminalEvidence,
    TransportKind,
    TransportLimits,
)

def _split_canonical_lines(text: str) -> tuple[str, ...]:
    """Split fully-reassembled text on explicit CRLF/CR/LF boundaries only.

    Deliberately NOT ``str.splitlines()``: its broader "universal newlines"
    set also splits on ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``, U+0085,
    U+2028, U+2029, which would incorrectly treat a literal control
    character in fake-peer payload bytes as a line break (cross-review
    finding, ag+cx, 2026-08-02). Trailing-empty-line behavior still matches
    ``splitlines()``: a single trailing terminator does not produce an
    extra empty final element (verified against DT-02 and unit tests).
    """
    if not text:
        return ()
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if normalized.endswith("\n"):
        lines = lines[:-1]
    return tuple(lines)


class FakeOutputDecoder:
    """Decoder implementation for FakePeerAdapter."""

    def __init__(self) -> None:
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")()
        self._text_parts: list[str] = []
        self._events: list[DecoderEvent] = []
        self._finalized = False

    def feed(self, chunk: bytes, *, channel: OutputChannel = OutputChannel.STDOUT) -> tuple[DecoderEvent, ...]:
        if self._finalized:
            raise RuntimeError("feed called after finalize")
        if type(chunk) is not bytes:
            raise ValueError("chunk must be bytes")
        if not isinstance(channel, OutputChannel):
            raise ValueError("channel must be OutputChannel")

        text = self._utf8_decoder.decode(chunk)
        if not text:
            return ()

        self._text_parts.append(text)
        event = DecoderEvent(
            kind=DecoderEventKind.ASSISTANT_TEXT,
            payload={"text": text, "channel": channel.value},
        )
        self._events.append(event)
        return (event,)

    def finalize(self) -> DecodedOutput:
        if self._finalized:
            raise RuntimeError("finalize already called")
        self._finalized = True

        tail = self._utf8_decoder.decode(b"", final=True)
        if tail:
            self._text_parts.append(tail)

        canonical_text = "".join(self._text_parts)
        return DecodedOutput(
            canonical_text=canonical_text,
            canonical_lines=_split_canonical_lines(canonical_text),
            events=tuple(self._events),
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
    ``PeerAdapter`` Protocol shape. ``interpret_chunk``/
    ``finalize_decoded_output`` are real (Step 3); the remaining
    parsing/planning methods stay ``NotImplementedError`` (see module
    docstring).

    Channels are treated as one ordered stream: ``channel`` is recorded on
    each emitted event but STDOUT/STDERR/PTY chunks all feed the same
    decoder and the same canonical text/line buffer (cross-review finding,
    ag+cx, 2026-08-02). This fake peer's own deterministic scripts only
    ever write one channel, so real interleaving never happens against
    DT-02 or any other current test; a genuinely channel-interleaved fake
    would need separate decoder/buffer state per channel, deliberately not
    built here since nothing tests or specifies that behavior yet.
    """

    descriptor: PeerDescriptor = _FAKE_DESCRIPTOR

    def __init__(self) -> None:
        # Expose the legacy incremental decode path using an internal decoder.
        self._internal_decoder = FakeOutputDecoder()

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        return PromptPolicy(
            policy_id="fake-standard-policy",
            max_inline_utf8_bytes=1000000,
            artifact_reference_supported=False,
        )

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan:
        import sys
        
        return InvocationPlan(
            argv=(
                sys.executable,
                "tools/fake_peer/pipe_executable.py",
                "--stdout",
                request.prompt_content or "FAKE_PEER_STDOUT\n",
                "--exit-code",
                "0",
            ),
            cwd_reference=request.workspace_scope,
            environment_delta={},
            transport=TransportKind.PIPE,
            stdin_payload=None,
            limits=limits,
            redacted_display="python pipe_executable.py",
            artifacts=(),
            session_action=request.requested_session_action,
        )

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
        return FakeOutputDecoder()

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        # Basic pattern-based success classification for the test double.
        success = (
            process.execution_outcome.exit_code == 0
            and not process.execution_outcome.timed_out
            and not process.execution_outcome.cancelled
        )
        return ProtocolAssessment(
            parsed=True,
            response_present=len(raw_chunks) > 0,
            vendor_completion_marker=success,
            suspected_truncation=process.execution_outcome.timed_out or process.execution_outcome.cancelled,
            protocol_failure=None if success else ErrorCode.VENDOR_PROTOCOL_FAILURE,
        )

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
        """Feed one raw byte chunk; return events observed from it.

        Uses ``codecs.getincrementaldecoder("utf-8")`` -- the standard
        library's purpose-built tool for exactly DT-02's problem (a
        multi-byte UTF-8 character split across a chunk boundary). CR/LF
        line-boundary splitting is NOT done per-chunk: chunk1 ending in
        ``\\r`` and chunk2 starting with ``\\n`` (DT-02) must combine into
        one line break. Correctness relies on never splitting lines until
        ``finalize_decoded_output`` normalizes line endings once over the
        fully reassembled text, at which point the chunk boundary is gone
        and the boundary-CRLF is a non-issue.

        Event-emission granularity (one ASSISTANT_TEXT event per non-empty
        decoded chunk) is a reasonable, conservative reading, not something
        either ratifying doc mandates -- DT-02 does not assert on
        ``events``, only ``canonical_lines``. Emitted events are also
        accumulated and returned in the terminal ``DecodedOutput.events``.
        """
        try:
            return self._internal_decoder.feed(chunk, channel=channel)
        except RuntimeError as e:
            raise RuntimeError("interpret_chunk called after finalize_decoded_output") from e

    def finalize_decoded_output(self) -> DecodedOutput:
        try:
            return self._internal_decoder.finalize()
        except RuntimeError as e:
            raise RuntimeError("finalize_decoded_output already called") from e
