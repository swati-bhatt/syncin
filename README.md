# Syncin

**A fault-tolerant, multi-tenant scheduled-delivery engine** — the distributed-systems core of an
appointment-reminder product. It schedules reminders, delivers them through a swappable messaging
provider, survives provider failures with retries / backoff / dead-lettering, skips reminders
already recorded as sent when a job is redelivered, and processes customer replies — multi-tenant,
with idempotency at every hop (request, schedule, send and reply).

> Runs 100% locally at **$0** via Docker Compose (Postgres + Redis + app). The default provider is an
> in-process **mock** with a configurable failure-injection rig that drives reliability experiments; a
> Twilio WhatsApp sandbox adapter is a planned drop-in (the provider interface is already in place).

`Node · TypeScript · Fastify · PostgreSQL/Prisma · Redis/BullMQ · Docker`

**Paper:** [Retry Backoff Under Failure](https://swati-bhatt.github.io/syncin/) — the measurement study, revised October 2026.

---

## Why it's interesting

Sending a reminder is easy. Sending it **reliably under failure** is the point of this project:

- **Multi-tenancy** — every row scoped by `tenant_id`. `/v1` derives the tenant from a verified API key, never from client input. The inbound webhook carries the tenant in its URL; provider signature verification is planned hardening. `/metrics` is engine-wide and unauthenticated by design (network-restricted in production).
- **Idempotency at every hop** — (1) *request*: an optional `Idempotency-Key` header, reserved before the work (Stripe-style: a retry with the same key creates no duplicate booking; without the header, a retry books again); (2) *schedule*: one reminder per (event, kind), and the BullMQ `jobId` is the reminder row id; (3) *send*: a SEND key checked before the send and claimed in the success transaction, so a redelivered job skips any reminder whose send is already recorded; (4) *reply*: inbound webhooks deduped on the provider message id.
- **Delayed scheduling** — two reminders (T-24h, T-2h) computed per appointment and enqueued as delayed jobs.
- **Retries + equal-jitter exponential backoff** — the default (equal jitter as named by Brooker, 2015) keeps a deterministic floor of half of each exponential wait (a guaranteed share of the schedule's outage coverage) and jitters the other half to spread the retry herd; it was *chosen by the project's own measurement study* (below). The study used 8 attempts; the shipped default of 5 attempts gives waits that reach only 7.5–15 s. Coverage scales with the attempt budget, so size `REMINDER_MAX_ATTEMPTS` / base / cap to the outage you must survive.
- **Dead-letter queue + replay** — BullMQ has no built-in DLQ, so Syncin builds one from BullMQ's retained failed set, a `DEAD` state in Postgres, and a replay endpoint: permanently-failing sends are parked, inspectable, and replayable once the issue clears.
- **Inbound replies** — customers reply `YES`/`NO` via webhook; their nearest upcoming appointment is confirmed/cancelled (deduped), and a cancel stops its pending reminders. Known limitation: a `NO` after a `YES` is not handled — the event stays `CONFIRMED` and the T-2h reminder still sends.
- **Graceful shutdown** — `SIGTERM` drains the worker and closes connections cleanly.
- **Observability** — `GET /metrics` exposes queue depth, job states, failed-attempt counts, and dead-letter size; `GET /metrics/attempts` returns the mock provider's per-attempt log that the measurement study reads.

## Architecture

```mermaid
flowchart LR
    Client(["Client / app"]) -->|"POST /v1/events"| API["API · Fastify"]
    API -->|"save event + reminders"| PG[("Postgres<br/>source of truth")]
    API -->|"enqueue T-24h / T-2h"| Q[["Redis · BullMQ<br/>delayed queue"]]
    Q -->|"timer fires"| W["Worker<br/>retry · backoff · DLQ"]
    W -->|"send"| P["Provider<br/>mock / Twilio"]
    W -->|"write message_log"| PG
    P -->|"text"| Cust(["Customer"])
    Cust -.->|"reply YES / NO"| WH[/"Inbound webhook"/]
    WH -->|"confirm / cancel"| PG
```

A booking is written to Postgres (source of truth) and its two reminders are enqueued in Redis as
delayed jobs. When a timer fires, the worker pulls the job, sends via the provider, and logs it —
retrying on failure and dead-lettering after exhaustion. Customer replies return through a webhook
and update the appointment.

## Reliability — what happens when things go wrong

| Failure | Engine's response |
|---|---|
| Transient send failure | retried on an **equal-jitter** exponential schedule (study-selected default; 6 strategies switchable via `REMINDER_BACKOFF_STRATEGY`: `none`, `fixed`, `exp`, `full_jitter`, `equal_jitter`, `decorrelated`; unknown names fall back to `full_jitter`) |
| Permanent / retries exhausted | **dead-lettered** (parked, inspectable), replayable via `POST /v1/reminders/replay` |
| Worker crashes mid-job | BullMQ redelivers the job (at-least-once); the **send-idempotency key** skips a reminder whose send is already recorded. A crash after the provider accepts but before the commit can still re-send (see Design notes) |
| Duplicate inbound webhook | deduped on the provider message id |
| Customer cancels | pending reminders are **skipped** at send time (a `NO` after a `YES` is not handled yet) |
| `SIGTERM` | worker drains in-flight jobs, then connections close |

## Tech stack & the reasoning

| Tool | Role | Why this one |
|---|---|---|
| **Node + TypeScript** | runtime | the workload is I/O-bound (DB / queue / network) — Node's sweet spot |
| **Fastify** | HTTP framework | fast, built-in request-schema validation, first-class TypeScript |
| **PostgreSQL + Prisma** | source of truth | transactions + unique constraints are what *guarantee* no duplicate send records, and no duplicate bookings when the client sends an Idempotency-Key; the data is relational |
| **Redis + BullMQ** | job queue | durable **delayed** jobs with retries/backoff that survive restarts (default Redis persistence; AOF is a hardening item) |
| **Docker Compose** | packaging | one command, identical anywhere, $0 |

## Quickstart

Requires Docker.

```bash
make up     # build + start Postgres, Redis, and the app
```

Create an appointment two days from now (seeded demo tenant key: `sk_demo_tenant_key_123`):

```bash
START=$(date -u -v+2d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+2 days' +%Y-%m-%dT%H:%M:%SZ)
KEY=demo-$(date +%s)
curl -X POST localhost:3000/v1/events \
  -H "Authorization: Bearer sk_demo_tenant_key_123" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"recipient\":{\"name\":\"Jane Doe\",\"phone\":\"+15551234567\"},\"startsAt\":\"$START\"}"
```

Run the `curl` again in the same shell (same `$KEY`) and you get the identical response, with no
duplicate booking. `startsAt` must be in the future (422 otherwise). A request the service rejects
still consumes its Idempotency-Key (a retry with that key gets 409), so set a fresh `KEY` after a 4xx.

Then `make logs`, `make psql`, `make down`, `make clean`. Full list in [COMMANDS.md](COMMANDS.md).

## Lifecycle of a reminder

1. `POST /v1/events` → the API validates, find-or-creates the recipient, and writes the event + two `PENDING` reminder rows **in one transaction**.
2. After commit, two **delayed** BullMQ jobs are enqueued (T-24h, T-2h); the rows flip to `SCHEDULED`. (Times already in the past are skipped.)
3. A timer fires → the **worker** loads the event + recipient, checks the send-idempotency guard, calls the provider, writes a `message_log` row, marks `SENT`.
4. On failure → retry with jittered backoff; after N attempts (or a permanent error) → `DEAD` (dead-letter).
5. The customer replies → `POST /webhooks/:tenantId/inbound` → deduped, matched to their appointment, `CONFIRMED` / `CANCELLED`.

## Measurement study — [paper](https://swati-bhatt.github.io/syncin/) · [full report](experiments/REPORT.md)

72 controlled runs (isolated Docker stacks, 100 synchronized reminders each) compare six
retry-backoff strategies — none, fixed, exponential, and full / equal / decorrelated jitter as
named and compared by Brooker (2015) — under steady transient failure and a provider outage.
Headline results:

- **Steady failure: strategy choice barely matters.** Delivery ≈ 1−fⁿ and provider cost
  ≈ 1/(1−f) regardless of waits — backoff only shifts tail latency (p99 0.85 s for
  immediate retry vs 15.6–16.9 s for exponential/decorrelated at 50% failure).
- **Provider outage (effective W ≈ 34 s): strategy choice is everything.** The mock provider
  fails every call until it has been up for 60 s, then recovers fully. The reminders were
  scheduled 25 s after creation, so the synchronized cohort made its first attempt about 26 s
  after boot and faced an effective outage of **W ≈ 34 s** (recovery is bracketed 32.6–34.5 s
  after the first attempt in the jittered runs). With 8 attempts: immediate & fixed retry
  deliver **0%**, full jitter (which made the fewest calls in Brooker's comparison) **32%**,
  decorrelated 73%, exponential and equal jitter **100%**.
- **Survival is schedule coverage, and a model predicts it.** A job survives iff the sum of
  its attempt budget's waits is at least W. Exponential reaches 45 s by attempt 7 and 60 s by
  attempt 8; every job succeeded on attempt 7, with one attempt to spare. Full jitter's seven
  waits sum to ≈ 30 s on average, short of 34 s for most jobs. A Monte Carlo of the engine's
  own `computeBackoff` matches all six measured results (table below).
- **Exponential survives but stampedes.** Its retries stay in lockstep: the whole cohort
  reaches the provider at its next scheduled pulse (45 s), about 12 s after the provider had
  already recovered, all within 0.36–0.60 s.
- **Equal jitter (exp/2 + U(0, exp/2)) gets both, at this outage length.** At W ≈ 34 s it
  matched exponential's 100% survival with a ~5× smaller recovery peak (mean 18.67 vs 100).
  Its floors guarantee a reach of 30 s, expected ≈ 45 s, maximum just under 60 s. It did not
  survive on its floor alone (30 s < 34 s): the random half had to add about 3.6 s more
  (2.6–4.5 s across the recovery bracket), which the model says happens for 99.95% of jobs.
- **Full jitter's calm is partly attrition.** Its low recovery peak (8–9) is partly because
  only 26–38 jobs were still alive at recovery. Per surviving job (0.265) its jitter does spread
  arrivals compared with exponential (1.0), but it is not calmer than equal jitter (0.187).
  During the outage it sent more calls than exponential (762.3 vs 600 before the first
  success), with about twice the 1-s peak (199 vs 100).

**Peak recovery load** = the most provider attempts in any 1-second sliding window at or after
the cohort's first successful delivery. Exponential's whole cohort lands inside one such window,
so its 100 is capped by N = 100 and the ~5× ratio (5.36) grows at finer windows.

| Strategy | Sum of the 7 waits (s) | Model: survival at W = 33.6 s (range over the bracket) | Measured survival (3 runs) | Peak recovery load (3 runs) | Model: survival at W = 60 s |
|---|---|---|---|---|---|
| none | 0 | 0% | 0% (0, 0, 0) | – | 0% |
| fixed | 7 | 0% | 0% (0, 0, 0) | – | 0% |
| exponential | 60 (45 by attempt 7) | 100% | 100% (100, 100, 100) | 100, 100, 100 | 100% (boundary) |
| full jitter | mean ≈ 30, sd ≈ 8 | 33.34% (29.27–37.6) | 32% (38, 26, 32) | 9, 8, 8 | 0% |
| equal jitter | 30 guaranteed, mean ≈ 45, max < 60 | 99.95% (99.79–99.99) | 100% (100, 100, 100) | 15, 23, 18 | 0% |
| decorrelated | mean ≈ 46.5, sd ≈ 18.9 | 70.27% (68.84–71.66) | 73% (74, 71, 74) | 11, 13, 12 | 27.86% |

Model assumptions: waits only, using the exact `computeBackoff` formulas; it ignores per-attempt
processing time and worker queueing; W comes from the jittered runs' recovery bracket; the cohort
starts in sync. Every strategy's pooled measured survival lies within the model's bracket range
± 2 binomial SE (n = 300).

![Provider outage, effective W ≈ 34 s: survival by strategy](experiments/results/figures/fig3_outage.png)
![Retry arrivals through the outage, with the shared recovery line at W ≈ 33.6 s](experiments/results/figures/fig4_herd.png)
![MODEL, not measurement: predicted survival vs effective outage W at 8 attempts, measured runs overlaid](experiments/results/figures/fig5_coverage_model.png)

Fig 5 is a **model, not a measurement**: lines are the Monte Carlo, dots are the measured runs.
It answers "what about a true 60 s outage?": at W = 60 s the model gives full jitter ≈ 0%,
equal jitter ≈ 0%, decorrelated 27.86%, and exponential sits exactly at its boundary (its 8th
attempt lands at 60 s). Equal jitter's guaranteed floor needs 12 attempts (11 waits) to reach 60 s.

**Prior work and scope.** Full, equal and decorrelated jitter are not new: Brooker (2015) named
and compared them by simulating many clients contending to update one database row and retrying
until they succeeded. In that setting full jitter did the least work, decorrelated jitter finished
slightly sooner, and equal jitter took the longest of the three. Syncin studies a different setting: a
synchronized cohort facing a provider outage under a fixed attempt budget, where what matters is
whether the budget's waits outlast the outage. There, equal jitter's floor of half of each
exponential wait keeps enough of the schedule's reach to preserve coverage at the measured outage
length. The floor fraction is a dial: raise it for more coverage and less smoothing; lower it for
more smoothing and less coverage. Neither variant was measured.

**Limits.** Equal jitter was added after seeing the first five strategies' outage results and was
evaluated on fresh runs in the same single scenario; the engine default was switched on that
evidence, so it is not a held-out test. One outage length was measured; fig 5 extends it by model
only. A fixed attempt budget favours long schedules: given the same expected total wait
(10 attempts), full jitter's predicted survival at W ≈ 33.6 s is 86.78%. N = 100 × 3 per cell, so
outage-regime differences below ~7 pp are not resolvable (~3 pp near the delivery ceiling). Mock
provider, single host, synchronized cohort (the worst case for herds).

Raw per-run data for all 72 runs, the harness, and the analysis script live under
[`experiments/`](experiments/). `analyze.py` regenerates the summary tables, `derived.json` /
`derived.md` (every number above) and the five figures:

```bash
pip install numpy matplotlib
python3 experiments/analyze.py && THEME=dark python3 experiments/analyze.py
```

`run-one.sh` skips any config whose result file already exists, so to re-run the sweep from
scratch, move the committed raw data aside first:
`mv experiments/results/raw experiments/results/raw.orig && mkdir experiments/results/raw && ./experiments/sweep.sh 3`
(72 isolated runs, 3 in parallel). Failure injection is random and unseeded, so a re-run
reproduces rates, not exact counts; only the outage window and the name triggers are deterministic.

**Reference.** Brooker, M. (2015, March 4). *Exponential Backoff And Jitter.* AWS Architecture Blog.
https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/

## Project structure

```
prisma/
  schema.prisma                the data model: Tenant · Member · Recipient · Event ·
                               ReminderJob · MessageLog · IdempotencyKey
  migrations/                  committed migrations, applied by `prisma migrate deploy` at boot
  seed.ts                      demo tenant + API key (idempotent)
src/
  server.ts · app.ts           boot + wiring (API and worker in one process); graceful shutdown
  config/ · db/ · lib/         env, Prisma client, errors, idempotency helpers
  plugins/tenant.ts            Bearer-key → tenant resolution
  modules/events/              create flow: routes → controller → service (+ request schema)
  modules/reminders/
    reminder.scheduler.ts      plans the two reminder rows, enqueues the delayed jobs
    reminder.sender.ts         send + send-idempotency guard
    reminder.replay.ts         dead-letter replay + single redelivery
    reminder.routes.ts         POST /v1/reminders/replay · /v1/reminders/:id/redeliver
  modules/inbound/             reply webhook (confirm / cancel, deduped)
  modules/metrics/             GET /metrics · GET /metrics/attempts
  providers/                   swappable provider interface + mock + failure rig
  queues/
    connection.ts              Redis connection
    reminder.queue.ts          the delayed queue
    reminder.worker.ts         worker loop + computeBackoff: retry · backoff · dead-letter
experiments/                   harness (run-one.sh, sweep.sh), analyze.py, REPORT.md, results/
```

## Design notes

- **Tenant = verified API key** on `/v1`, never a client-supplied id. The inbound webhook carries the tenant in its URL path; provider signature verification is planned hardening. Postgres Row-Level Security would be the production hardening; it is not implemented — isolation today is tenant-scoped queries.
- **Transactional-outbox (lite):** reminder rows commit *with* the event in one transaction (an outbox trail); the BullMQ jobs are enqueued after commit. The sweeper that would re-enqueue `PENDING` rows if enqueue never runs is designed but not built. Redis runs with default persistence (no AOF).
- **Exactly-once is impossible.** A redelivered job skips any reminder whose send is already recorded (DB send key, checked before the send and claimed in the success transaction). The remaining window — a crash after the provider accepts but before our commit — is the exactly-once impossibility. The reminder id is passed to the provider as its idempotency key, so a provider that dedupes can close it; the mock does not, and no real adapter exists yet. Two concurrent runs of one reminder (e.g. a manual redeliver while the original job is active) can also both send, because the send check is check-then-act.
- **Schema is versioned with committed Prisma migrations** (`prisma/migrations/`), applied by `prisma migrate deploy` at boot (`npm run db:push` remains for quick local prototyping).
- **One process today:** the API and the worker run in one process; scaling them independently needs a separate worker entrypoint.

## Roadmap

- [x] Measurement study — retry/backoff comparison ([report](experiments/REPORT.md))
- [ ] Twilio WhatsApp sandbox adapter (provider interface is ready)
- [x] Committed Prisma migrations
- [ ] Hardening: `PENDING`-row re-enqueue sweeper, webhook signature verification, Redis AOF, per-tenant rate limits
- [ ] Minimal dashboard UI

---

Built as a systems portfolio project. Runs entirely on free, local infrastructure.
