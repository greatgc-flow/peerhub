#!/usr/bin/env bash
# Run one profile once on a benchmark folder and append "profile rN secs SCORE" to <folder>/runs/results.txt.
# usage: docs/benchmarks/run_profile_bench.sh <bench-folder> <peer> <profile> <run-number> [extra peerhub ask args, e.g. --writable for ag]
# The ask runs with -w at the repo root (no reference.md is reachable from the task); the response is graded by <folder>/grade.py.
set -u
bench=$(cd "$1" && pwd); peer=$2; prof=$3; rep=$4; shift 4
name=${prof//./-}; out=$bench/runs; mkdir -p "$out"
root=$(git -C "$bench" rev-parse --show-toplevel)
start=$(date +%s)
peerhub ask "$peer" --profile "$prof" --stream "$(basename "$bench")-$name-r$rep" --request-id "$(basename "$bench")-$name-r$rep" \
  -w "$root" --timeout-seconds 1800 "$@" --query-file "$bench/task.md" > "$out/$name-r$rep.md" 2>&1
end=$(date +%s)
sed -i '/^session: /d; s/\r$//' "$out/$name-r$rep.md"
score=$(cd "$bench" && python -I -B grade.py "$out/$name-r$rep.md" 2>&1 | tail -1)
echo "$prof r$rep $((end-start))s $score" | tee -a "$out/results.txt"
