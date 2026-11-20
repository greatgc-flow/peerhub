# PeerHub benchmark suite v1

The suite has six fixed tasks with ten hidden cases each.

| Task | Difficulty | Topic |
| --- | --- | --- |
| [E1](E1/task.md) | Easy | Canonical Roman numerals |
| [E2](E2/task.md) | Easy | Difference of closed integer intervals |
| [M1](M1/task.md) | Medium | Astronomical Gregorian date arithmetic |
| [M2](M2/task.md) | Medium | Pricing rules with an explicit precedence exception |
| [H1](H1/task.md) | Hard | Preemptive priority round robin |
| [H2](H2/task.md) | Hard | Weighted cyclic two-player game |

## Run a benchmark

1. Use a separate peer task workspace without access to the grader checkout.
   Give the peer only the contents of the selected `task.md` and save the reply.
   Keep `cases.json`, `reference.py`, and the grader on the grading host or in a
   filesystem scope inaccessible to the peer. Record the elapsed peer time,
   effective profile, model and effort, and peer CLI version at the time of the ask.
2. After the peer returns, run `grade` or `record` from the grader checkout.
   `grade` extracts the first fenced `python` block and reports `SCORE n/N`.
   `record` grades the reply and appends its measurement.

```text
python -m tools.suite_runner grade --suite docs/benchmarks/suite-v1 --task H1 --response reply.md
python -m tools.suite_runner record --task H1 --response reply.md --profile PROFILE --model MODEL --effort EFFORT --peer-cli-version VERSION --wall-seconds 42.5
python -m tools.suite_runner verify
```

The runner uses a fresh isolated Python worker per case with a 0.45-second
timeout and a 4.75-second task budget. The parent keeps expected values and sends
only the source and a single case's arguments. An audit hook blocks file opens
except for standard-library imports, as well as socket operations, subprocess
launches, and file writes. Use an OS sandbox for replies that require a security
boundary against hostile Python code.

`record` uses the effective settings supplied by the caller, including any model
or effort overrides. Pass `--effort none` for an unset effort. `wall_seconds` is
the time the peer took to answer. Each line in `results.jsonl` is a UTF-8 JSON
object with `suite_version` (`suite-v1`), `task`, `profile`, `model`, `effort`,
`peer_cli_version`, `score` (`n/N`), `wall_seconds`, and `date` (UTC timestamp).
The file first appears when a measurement is recorded. `peerhub profile stats`
uses this path for `last_benchmark_run`.

`verify` runs all six references through the grading path and exits nonzero if
any case fails. Each `cases.json` is an array of objects with positional `args`
and an `expected` value. Arrays represent tuples and interval lists as specified
by the task signatures. H1 represents its integer-key map as sorted
`[job_id, finish_time]` pairs. Integers are decimal JSON numbers and require
exact arithmetic beyond IEEE 754's safe integer range.

The hard tasks are designed to distinguish even the strongest profiles. Their
actual score distribution awaits the owner-approved baseline after release.

## Draft corrections and verification

| Task | What changed and how it was checked |
| --- | --- |
| E1 | Replaced `VIV` with a whitespace case, retaining noncanonical subtractive, case, and range checks. Enumerated all 3,999 canonical strings by place value. |
| E2 | Stated the sorting-scale bound and added an unsorted mix of overlap, adjacency, ignored inverted ranges, and cuts. Checked small cases and 2,000 random inputs by set enumeration, and the `10**18` endpoints by partitioning at interval boundaries. Grading checks that both inputs retain their values. |
| M1 | Clarified that the output year can exceed the input bounds. Replaced the redundant large backward whole-cycle offset with `-10**18` days, which lands on `(-3737907006988508, 5, 13, 4)`. Checked the cases and 2,500 random inputs with a January-based 400-year calendar table and checked the table against `datetime` on 1,000 in-range dates. |
| M2 | Made overrides clause-specific and R6's final-floor precedence explicit. The planted R7 conflict remains, so a nonempty VIP order pays 100 cents. Added distinct BOGO item rows and an exact-arithmetic test above the floating-point exact integer range. Recomputed all cases with expanded billed units and rational discounts and tax. |
| H1 | Retained simultaneous-event cases and added a long run with unequal work, preserved quantum on preemption, and a same-priority arrival during preemption. Checked against a tick simulator on 5,000 random schedules and on the fixed cases after reducing repeated complete rotations in the large cases. The reference names the running job's state and skips whole rotations. |
| H2 | Defined the cost objectives for both players and the winner's obligation to force termination through zero-cost cycles. Added a 6,000-edge chain for depth and wide accumulated costs. Checked small cases and 2,500 random graphs with finite-horizon minimax and checked the chain's result from edge parity and total weight. |

All retained draft expectations agreed with the independent checks. E2, H1,
and H2 describe their input scale and rely on the runner's shared time budget.
