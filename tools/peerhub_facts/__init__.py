"""peerhub fact-refresh routine (implements docs/design/FACT-REFRESH-PROCEDURE-R1.md).

Manually-invoked drift detector. Collects observed facts about the peer
CLIs, the adapter decoders, the installed dependency set, and the test
suite, compares them against the semantic expectations recorded in
``docs/compatibility/peer-cli-contracts.toml``, and writes a report under
``build/peerhub-facts/``.

Nothing is auto-applied except generating those reports: expected
versions, required flags, decoder schemas, and dependency constraints are
never rewritten by this routine, because doing so would bless observed
drift merely because it was observed -- the exact failure mode the
routine exists to catch (procedure, "Output contract").
"""

from __future__ import annotations
