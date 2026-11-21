# Plain monitor golden fixtures

[`plain_compact_sections.json`](plain_compact_sections.json) and
[`plain_expand_sections.json`](plain_expand_sections.json) store the expected
text for the sections of the compact and expanded plain monitor frames. The
tests identify each block using the renderer's section catalog. When height
eviction changes the retained sections or their lengths, the height tests
check the section priority and overflow counts.

Run this command from the repository root to regenerate the POOLS blocks:

```powershell
python -m tests.communication.fixtures.monitor.regenerate_plain_sections
```

The script uses the same payloads and widths, heights, and encodings as the
golden tests. It replaces, adds, or removes the POOLS block for each case and
preserves the text and order of the other stored blocks.
