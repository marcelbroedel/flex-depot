#!/bin/bash
# ================================
# Flex-Depot illustrative example: run S1 -> S4 (bidirectional) and their
# unidirectional companions S1_uni -> S4_uni sequentially, then aggregate.
# The *_uni runs reuse each scenario's TOML but point at the unidirectional
# flexibility band; aggregate_results.py pairs them for the bidirectional-vs-
# unidirectional comparison (#8).
# Fail fast: abort on the first failing scenario (non-zero exit code).
# Expect ~30-45 min per scenario with HiGHS (1-month window); 8 runs total.
# All outputs land in results/illustrative_example/.
# ================================

set -u

# Go to the repository root (data paths in the TOMLs are repo-relative)
cd "$(dirname "$0")/../.." || exit 1

OUT="results/illustrative_example"
mkdir -p "$OUT"
INDEX="$OUT/run_index.csv"
echo "scenario,run_dir,runtime_s" > "$INDEX"

for SID in s1 s2 s3 s4 s1_uni s2_uni s3_uni s4_uni; do
    CONFIG="examples/illustrative_example/settings_${SID}.toml"
    RUN_DIR="$OUT/$SID"
    echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Starting scenario ${SID} (${CONFIG}) -- expect ~30-45 min with HiGHS ==="
    T0=$(date +%s)

    python -m flex_dep_opt run-sim --config "$CONFIG" --run-dir "$RUN_DIR" || {
        echo "ERROR: scenario ${SID} simulation failed -- aborting (remaining scenarios skipped)." >&2
        exit 1
    }
    python -m flex_dep_opt run-post --config "$CONFIG" --run-dir "$RUN_DIR" || {
        echo "ERROR: scenario ${SID} postprocessing failed -- aborting (remaining scenarios skipped)." >&2
        exit 1
    }

    T1=$(date +%s)
    echo "${SID},${RUN_DIR},$((T1 - T0))" >> "$INDEX"
    echo "=== Scenario ${SID} finished in $((T1 - T0)) s -> ${RUN_DIR} ==="
done

python examples/illustrative_example/aggregate_results.py "$INDEX" || exit 1
