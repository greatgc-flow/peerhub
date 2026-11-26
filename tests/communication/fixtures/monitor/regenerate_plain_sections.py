"""Refresh the POOLS blocks in the plain monitor goldens."""
from __future__ import annotations

import json

from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.cli.monitor_view import render_grouped_frame
from tests.communication.diag import test_monitor_expand_rich as expanded
from tests.communication.diag import test_monitor_plain_golden as compact


def main() -> None:
    pools = FRAME_SECTIONS["pools"]
    for module, cases in ((compact, compact.golden_cases()), (expanded, expanded.plain_cases())):
        source = module.GOLDEN.read_bytes()
        fixtures = json.loads(source)
        changed = 0
        for name, payload, options in cases:
            wide = module is expanded
            expected = compact.blocks(fixtures[name], expanded=wide)
            actual = compact.blocks(render_grouped_frame(payload, **options), expanded=wide)
            new_pool = actual.get(pools)
            if new_pool == expected.get(pools):
                continue
            if new_pool is None:
                del expected[pools]
            elif pools in expected:
                expected[pools] = new_pool
            else:
                # A newly retained POOLS block starts the body. Keep the stored order of the other blocks.
                expected = {"top": expected.pop("top"), pools: new_pool, **expected}
            fixtures[name] = "\n".join(line for block in expected.values() for line in block) + "\n"
            changed += 1
        if changed:
            newline = "\r\n" if b"\r\n" in source else "\n"
            output = json.dumps(fixtures, indent=1, ensure_ascii=False) + "\n"
            module.GOLDEN.write_bytes(output.replace("\n", newline).encode("utf-8"))
        print(f"{module.GOLDEN.name}: {changed} POOLS blocks changed")


if __name__ == "__main__":
    main()
