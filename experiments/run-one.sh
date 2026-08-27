#!/usr/bin/env bash
# Run ONE experiment configuration on an ISOLATED stack and record its results.
#
#   ./experiments/run-one.sh <strategy> <failure_rate> <repeat> [slot]
#
# strategy: none | fixed | exp | full_jitter | decorrelated
# slot:     0..N — each slot gets its own compose project + ports, so runs can
#           execute in parallel without touching each other (or the dev stack).
#
# Env overrides:
#   N_REMINDERS (100)  PERMANENT_RATE (0)  RECOVER_AFTER_MS (0)  LATENCY_MS (0)
#   LEAD_S (40) — reminders all come due LEAD_S seconds after the create burst,
#                 at the SAME instant (that synchronization is what makes retry
#                 herd effects measurable)
#   MAX_ATTEMPTS (5)   TIMEOUT_S (180)
#
# Output: experiments/results/raw/<config>_rep<r>.json
set -euo pipefail
STRATEGY=$1; FAILURE=$2; REPEAT=$3; SLOT=${4:-0}
N=${N_REMINDERS:-100}; PERM=${PERMANENT_RATE:-0}; RECOVER=${RECOVER_AFTER_MS:-0}
LAT=${LATENCY_MS:-0}; LEAD=${LEAD_S:-40}; ATT=${MAX_ATTEMPTS:-5}; TIMEOUT=${TIMEOUT_S:-180}

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="syncin-exp-$SLOT"
APP_PORT=$((3100 + SLOT)); PG_PORT=$((5600 + SLOT)); RD_PORT=$((6700 + SLOT))
BASE="http://localhost:$APP_PORT"; KEY="sk_demo_tenant_key_123"
OUT="$DIR/experiments/results/raw/${STRATEGY}_f${FAILURE}_p${PERM}_rec${RECOVER}_att${ATT}_rep${REPEAT}.json"

dc() { docker compose -p "$PROJ" -f "$DIR/docker-compose.yml" "$@"; }
psqlc() { dc exec -T postgres psql -U syncin -d syncin -P pager=off -tAc "$1"; }
cleanup() { dc down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[$PROJ] $STRATEGY f=$FAILURE perm=$PERM rec=${RECOVER}ms att=$ATT rep=$REPEAT (N=$N)"
REMINDER_BACKOFF_STRATEGY=$STRATEGY REMINDER_MAX_ATTEMPTS=$ATT \
MOCK_FAILURE_RATE=$FAILURE MOCK_PERMANENT_RATE=$PERM \
MOCK_LATENCY_MS=$LAT MOCK_RECOVER_AFTER_MS=$RECOVER \
POSTGRES_HOST_PORT=$PG_PORT REDIS_HOST_PORT=$RD_PORT APP_PORT=$APP_PORT \
  dc up -d --build >/dev/null 2>&1
curl -sS --retry 90 --retry-delay 2 --retry-connrefused --retry-all-errors "$BASE/ready" >/dev/null

# All N reminders come due at the SAME instant: startsAt = now + 2h + LEAD, so the
# T-2h reminder's runAt = now + LEAD. (The T-24h runAt is in the past → skipped.)
FIRE=$(python3 -c "import datetime as d;print((d.datetime.now(d.timezone.utc)+d.timedelta(hours=2,seconds=$LEAD)).strftime('%Y-%m-%dT%H:%M:%SZ'))")
T_CREATE_MS=$(python3 -c 'import time;print(int(time.time()*1000))')
for i in $(seq 1 "$N"); do
  curl -sS -o /dev/null -X POST "$BASE/v1/events" \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"recipient\":{\"name\":\"P$i exp\",\"phone\":\"+1777$(printf %07d "$i")\"},\"startsAt\":\"$FIRE\"}"
done

# Wait until no reminder is still in flight (fresh DB → every row is this batch).
DEADLINE=$(( $(date +%s) + LEAD + TIMEOUT ))
TIMED_OUT=false
while :; do
  LEFT=$(psqlc "select count(*) from reminder_jobs where state in ('PENDING','SCHEDULED','SENDING','FAILED');")
  [ "$LEFT" = "0" ] && break
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then TIMED_OUT=true; echo "[$PROJ] TIMEOUT ($LEFT unfinished)"; break; fi
  sleep 3
done

SENT=$(psqlc "select count(*) from reminder_jobs where state='SENT';")
DEAD=$(psqlc "select count(*) from reminder_jobs where state='DEAD';")
RETRIES=$(psqlc "select coalesce(sum(attempts),0) from reminder_jobs;")
# Delivery latency per delivered reminder: message logged − reminder due.
LATENCIES=$(psqlc "select coalesce(json_agg(round(extract(epoch from (ml.created_at - rj.run_at))::numeric,3)),'[]') from message_log ml join reminder_jobs rj on rj.id = ml.reminder_job_id where ml.direction='OUTBOUND';")
METRICS=$(curl -sS "$BASE/metrics")
ATTEMPTS=$(curl -sS "$BASE/metrics/attempts")

CONFIG_JSON=$(python3 - "$STRATEGY" "$FAILURE" "$PERM" "$RECOVER" "$LAT" "$ATT" "$N" "$REPEAT" <<'PY'
import json, sys
s, f, p, rec, lat, att, n, rep = sys.argv[1:9]
print(json.dumps({"strategy": s, "failure_rate": float(f), "permanent_rate": float(p),
                  "recover_after_ms": int(rec), "latency_ms": int(lat),
                  "max_attempts": int(att), "n_reminders": int(n), "repeat": int(rep)}))
PY
)
{ echo "$METRICS"; echo "$ATTEMPTS"; } | python3 - "$OUT" "$CONFIG_JSON" "$SENT" "$DEAD" "$RETRIES" "$TIMED_OUT" "$T_CREATE_MS" "$LATENCIES" <<'PY'
import json, sys
out, cfg, sent, dead, retries, timed_out, t_create, lats = sys.argv[1:9]
metrics = json.loads(sys.stdin.readline())
attempts = json.loads(sys.stdin.readline())
doc = {
  "config": json.loads(cfg),
  "result": {"sent": int(sent), "dead": int(dead), "retries": int(retries),
             "timed_out": timed_out == "true", "t_create_ms": int(t_create)},
  "latencies_s": json.loads(lats),
  "metrics": metrics,
  "provider_attempts": attempts["attempts"],
}
with open(out, "w") as fh: json.dump(doc, fh, indent=1)
print(f"[saved] {out}  sent={sent} dead={dead} retries={retries}")
PY
