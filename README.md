# Syncin

**A fault-tolerant, multi-tenant scheduled-delivery engine** — the distributed-systems core of an
appointment-reminder product. It schedules reminders, delivers them through a swappable messaging
provider, survives provider failures with retries / backoff / dead-lettering, never double-sends,
and processes customer replies — multi-tenant and idempotent end to end.

> Runs 100% locally at **$0** via Docker Compose (Postgres + Redis + app). The default provider is an
> in-process **mock** with a configurable failure-injection rig that drives reliability experiments; a
> Twilio WhatsApp sandbox adapter is a planned drop-in (the provider interface is already in place).

`Node · TypeScript · Fastify · PostgreSQL/Prisma · Redis/BullMQ · Docker`

---

## Why it's interesting

Sending a reminder is easy. Sending it **reliably under failure** is the point of this project:

- **Multi-tenancy** — every row scoped by `tenant_id`; the tenant is derived from a verified API key, never from client input.
- **Idempotency, two layers** — Stripe-style request idempotency on the API (no duplicate bookings on retry), and a send-side guard so the *at-least-once* queue never double-sends a reminder.
- **Delayed scheduling** — two reminders (T-24h, T-2h) computed per appointment and enqueued as delayed jobs.
- **Retries + exponential backoff + full jitter** — transient send failures retry on a jittered schedule (no thundering herd on a recovering provider).
- **Dead-letter queue + replay** — permanently-failing sends are parked, inspectable, and replayable once the issue clears.
- **Inbound replies** — customers reply `YES`/`NO` via webhook; the appointment is confirmed/cancelled (deduped), and a cancel stops its pending reminders.
- **Graceful shutdown** — `SIGTERM` drains the worker and closes connections cleanly.
- **Observability** — `GET /metrics` exposes queue depth, job states, retry counts, and dead-letter size.

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
| Transient send failure | retried with exponential backoff + **full jitter** |
| Permanent / retries exhausted | **dead-lettered** (parked, inspectable), replayable via `POST /v1/reminders/replay` |
| Worker crashes mid-job | BullMQ redelivers the job (at-least-once); the **send-idempotency key** prevents a double-send |
| Duplicate inbound webhook | deduped on the provider message id |
| Customer cancels | pending reminders are **skipped** at send time |
| `SIGTERM` | worker drains in-flight jobs, then connections close |

## Tech stack & the reasoning

| Tool | Role | Why this one |
|---|---|---|
| **Node + TypeScript** | runtime | the workload is I/O-bound (DB / queue / network) — Node's sweet spot |
| **Fastify** | HTTP framework | fast, built-in request-schema validation, first-class TypeScript |
| **PostgreSQL + Prisma** | source of truth | transactions + unique constraints are what *guarantee* no double-sends; the data is relational |
| **Redis + BullMQ** | job queue | durable **delayed** jobs with retries/backoff that survive restarts |
| **Docker Compose** | packaging | one command, identical anywhere, $0 |

## Quickstart

Requires Docker.

```bash
make up     # build + start Postgres, Redis, and the app
```

Create an appointment (seeded demo tenant key: `sk_demo_tenant_key_123`):

```bash
curl -X POST localhost:3000/v1/events \
  -H "Authorization: Bearer sk_demo_tenant_key_123" \
  -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" \
  -d '{"recipient":{"name":"Jane Doe","phone":"+15551234567"},"startsAt":"2026-09-01T14:30:00-04:00"}'
```

Then `make logs`, `make psql`, `make down`, `make clean`. Full list in [COMMANDS.md](COMMANDS.md).

## Lifecycle of a reminder

1. `POST /v1/events` → the API validates, find-or-creates the recipient, and writes the event + two `PENDING` reminder rows **in one transaction**.
2. After commit, two **delayed** BullMQ jobs are enqueued (T-24h, T-2h); the rows flip to `SCHEDULED`. (Times already in the past are skipped.)
3. A timer fires → the **worker** loads the event + recipient, checks the send-idempotency guard, calls the provider, writes a `message_log` row, marks `SENT`.
4. On failure → retry with jittered backoff; after N attempts (or a permanent error) → `DEAD` (dead-letter).
5. The customer replies → `POST /webhooks/:tenantId/inbound` → deduped, matched to their appointment, `CONFIRMED` / `CANCELLED`.

## Measurement study *(in progress)*

The failure-injection rig and `/metrics` exist to run controlled reliability experiments. The mock
provider takes env knobs — `MOCK_FAILURE_RATE`, `MOCK_PERMANENT_RATE`, `MOCK_LATENCY_MS`,
`MOCK_RECOVER_AFTER_MS` — so each run is just a different configuration.

A sample run: **20 reminders at a 40% send-failure rate → 17 delivered (85%)**, where a no-retry
system would land ~12 (60%), at a cost of 16 retries and 3 dead-lettered. A writeup comparing backoff
strategies (exponential vs full-jitter vs decorrelated-jitter) across failure rates is planned.

## Project structure

```
prisma/schema.prisma          the data model: Tenant · Recipient · Event ·
                              ReminderJob · MessageLog · IdempotencyKey
src/
  server.ts · app.ts          boot + wiring; graceful shutdown
  config/ · db/ · lib/         env, Prisma client, errors, idempotency helpers
  plugins/tenant.ts            Bearer-key → tenant resolution
  modules/events/              create flow: routes → controller → service
  modules/reminders/
    reminder.scheduler.ts      schedules the two delayed jobs
    reminder.worker.ts         worker loop: retry · backoff · dead-letter
    reminder.sender.ts         send + no-double-send guard
    reminder.replay.ts         dead-letter replay
  modules/inbound/             reply webhook (confirm / cancel, deduped)
  modules/metrics/             GET /metrics
  providers/                   swappable provider interface + mock + failure rig
  queues/                      Redis connection · queue · worker bootstrap
```

## Design notes

- **Tenant = verified API key**, never a client-supplied id. Postgres Row-Level Security is the production-grade hardening.
- **Transactional-outbox (lite):** reminder rows are written *with* the event in one transaction; the BullMQ jobs are enqueued after commit, with the `PENDING` rows as the recovery trail if enqueue never runs.
- **Exactly-once is impossible** — so the design layers a DB send-key (checked before send, claimed in the success transaction) with the provider's own idempotency key to get effectively at-most-once delivery on top of an at-least-once queue.
- Local dev uses Prisma `db push`; committed migrations are a planned follow-up.

## Roadmap

- [ ] Measurement paper — retry/backoff comparison (in progress)
- [ ] Twilio WhatsApp sandbox adapter (provider interface is ready)
- [ ] Committed Prisma migrations
- [ ] Minimal dashboard UI

---

Built as a systems portfolio project. Runs entirely on free, local infrastructure.
