#!/usr/bin/env bash
# The full measurement sweep, parallelized across isolated slots.
#   ./experiments/sweep.sh [parallel_slots]   (default 3)
#
# Part A — steady failure: 5 strategies × failure {0.1,0.3,0.5} × 3 repeats
# Part B — outage+recovery: provider hard-down for the first 60s of uptime, 8
#          attempts so backoff strategies can survive into recovery; herd shape
#          and survival read from the per-attempt timestamp log.
set -uo pipefail
P=${1:-3}
DIR="$(cd "$(dirname "$0")" && pwd)"
JOBS=$(mktemp)
for s in none fixed exp full_jitter decorrelated; do
  for f in 0.1 0.3 0.5; do
    for r in 1 2 3; do echo "steady $s $f $r" >> "$JOBS"; done
  done
done
for s in none fixed exp full_jitter decorrelated; do
  for r in 1 2 3; do echo "outage $s 0 $r" >> "$JOBS"; done
done
TOTAL=$(wc -l < "$JOBS" | tr -d ' ')
echo "sweep: $TOTAL runs across $P slots"

run_slot() { # consume every P'th job, offset by slot id
  local slot=$1 i=0
  # read via fd 9, and give the runs /dev/null stdin — `docker compose exec`
  # attaches stdin and would otherwise eat the rest of the job list (the classic
  # while-read pitfall; it cost us 57 of 60 runs the first time).
  while IFS=' ' read -u 9 -r kind s f r; do
    i=$((i+1)); [ $(( (i-1) % P )) -ne "$slot" ] && continue
    if [ "$kind" = "outage" ]; then
      RECOVER_AFTER_MS=60000 MAX_ATTEMPTS=8 LEAD_S=25 \
        "$DIR/run-one.sh" "$s" "$f" "$r" "$slot" </dev/null || echo "[slot $slot] FAILED: $s $f $r"
    else
      "$DIR/run-one.sh" "$s" "$f" "$r" "$slot" </dev/null || echo "[slot $slot] FAILED: $s $f $r"
    fi
  done 9< "$JOBS"
}
for slot in $(seq 0 $((P-1))); do run_slot "$slot" & done
wait
rm -f "$JOBS"
echo "sweep complete → $(ls "$DIR/results/raw" | wc -l | tr -d ' ') result files in experiments/results/raw/"
