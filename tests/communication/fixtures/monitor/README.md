# Plain monitor golden fixtures

[`plain_compact_sections.json`](plain_compact_sections.json) and
[`plain_expand_sections.json`](plain_expand_sections.json) store the expected
text blocks for each section of the compact and expanded plain monitor frames.
The tests use `in_order()` to re-sort the stored sections by the monitor's
`FRAME_HEADS` order before comparing them with the rendered frames. The stored
section order is therefore independent of the rendered order. The expanded
test keeps the trailing quota or hidden-row text in place.

There is no regeneration helper. The fixture rename did not regenerate either
file. Keep the JSON bytes identical when only the monitor's section order
changes.
