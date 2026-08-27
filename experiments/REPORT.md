# Retry backoff under failure: a measurement study of a reminder-delivery engine

*Syncin measurement report · August 2026*

## Abstract

We measure how the choice of retry-backoff strategy affects delivery reliability,
latency, and provider load in a queue-based delivery engine, under two failure regimes:
steady per-call transient failure and a 60-second full provider outage. Six strategies
share one budget (base 1 s, cap 15 s): immediate retry, fixed, exponential, full-jitter,
equal-jitter, and decorrelated-jitter. Under steady *uncorrelated* failure, strategy
choice barely matters: delivery converges to ≈ 1−fⁿ and cost to ≈ 1/(1−f) calls per
delivery regardless of waits — waiting only adds tail latency (p99 0.8 s for immediate
retry vs 15–17 s for exponential/decorrelated at f = 0.5). Under a *correlated* outage
the ranking inverts and separates sharply: with 8 attempts, immediate and fixed retry
deliver **0%** (their schedules exhaust inside the outage), full-jitter — the common
industry default — only **32%**, decorrelated 73%, and exponential 100%, because
survival is governed by *schedule coverage*: the (worst-case) sum of waits relative to
outage duration, which full jitter halves in expectation. Equal-jitter, which keeps a
deterministic floor of half the exponential schedule, recovers **100%** survival while
cutting the post-recovery retry stampede from 100 synchronized attempts/s (exponential)
to ~20/s. Backoff should be sized as a deadline-coverage problem first and jittered
above a floor second.

## 1. System under test

Syncin is a multi-tenant scheduled-delivery engine (Node/Fastify · PostgreSQL/Prisma ·
Redis/BullMQ). Creating an event transactionally persists reminder rows, then enqueues
delayed jobs; a worker (concurrency 5) delivers each due reminder through a provider
interface, retrying failures on a configurable backoff schedule, dead-lettering after a
fixed attempt budget, with a send-idempotency key preventing double-sends under the
queue's at-least-once semantics. The provider is an in-process mock with deterministic
failure injection: per-call failure probability, transient/permanent split, added
latency, and a "provider outage until uptime T" mode. Every provider attempt is recorded
with a millisecond timestamp and exposed via `/metrics/attempts`; engine state (job
states, retries, dead-letter size) via `/metrics`.

**Strategies measured** (`b` = base 1 s, `cap` 15 s, attempt *n*):

| Strategy | Wait before retry *n*+1 |
|---|---|
| `none` | 0 |
| `fixed` | `b` |
| `exp` | `min(cap, b·2ⁿ⁻¹)` |
| `full_jitter` | `U(0, min(cap, b·2ⁿ⁻¹))` |
| `equal_jitter` | `min(cap, b·2ⁿ⁻¹)/2 + U(0, ·/2)` |
| `decorrelated` | `min(cap, U(b, 3·prev))` |

## 2. Method

Each run executes on an **isolated stack** (own Docker Compose project, ports, and fresh
database volumes), created and destroyed per run. A run fires **N = 100** reminders whose
due time is the **same instant** (~40 s after creation), so failure and retry cohorts are
synchronized — the worst case for herd effects and the fair case for comparing strategies.
The run ends when no reminder remains in a non-terminal state; we then collect terminal
states, per-delivery latency (message-log time − due time), engine metrics, and the raw
provider attempt log.

**Scenarios.**
- *Steady transient failure:* each provider call fails independently with probability
  *f* ∈ {0.1, 0.3, 0.5}; attempt budget 5. Three repeats per (strategy, *f*).
- *Outage + recovery:* the provider fails **every** call for its first 60 s of uptime,
  then recovers fully; attempt budget 8 (schedule sums: exp = 60 s deterministic;
  full-jitter expectation ≈ 30 s). Three repeats per strategy.

**Injection validity.** Pooled over the steady runs, observed failure rates were
0.119 / 0.310 / 0.509 against configured 0.1 / 0.3 / 0.5.

## 3. Results

### 3.1 Steady transient failure: strategy choice barely matters

Every strategy delivered 96–100% at every failure rate (fig 1); at f = 0.5 all sit at
≈ 97–98%, consistent with the analytic ceiling 1−f⁵ = 96.9%. Provider cost is equally
flat: 1.08–1.14 calls per delivered message at f = 0.1 and 1.85–2.09 at f = 0.5 across
all strategies — with independent per-call failure, expected attempts per delivery is
1/(1−f) *no matter how long you wait*. What waiting does change is latency (fig 2):
immediate retry resolves everything sub-second even at f = 0.5 (p99 0.85 s), while
exponential and decorrelated drag p99 to 15.6 s and 16.9 s; full-jitter roughly halves
exponential's tail (p95 3.9 s vs 11.1 s at f = 0.5), with equal-jitter in between.

### 3.2 Outage + recovery: strategy choice is everything

| Strategy | Delivered (3 runs) | Peak load at recovery |
|---|---|---|
| none | **0%, 0%, 0%** | — (extinct before recovery) |
| fixed | **0%, 0%, 0%** | — (extinct before recovery) |
| full_jitter | 38%, 26%, 32% | 7–11 attempts/s |
| decorrelated | 74%, 71%, 74% | 12–14 attempts/s |
| exp | **100%, 100%, 100%** | **100 attempts/s** (synchronized wave) |
| equal_jitter | **100%, 100%, 100%** | 17–23 attempts/s |

Immediate retry spends all 8 attempts within ~1 s (800 wasted calls peaking at
480–730 attempts/s); fixed lasts 7 s. Both go extinct long before recovery.
Exponential's deterministic schedule (1+2+4+8+15+15+15 = 60 s) carries every job past
the outage — but its retries stay in lockstep: the attempt log shows clean synchronized
pulses at 1, 3, 7, 15, 30 and 45 s, and the entire surviving cohort hits the
just-recovered provider as a single 100-attempt/s wave (fig 4). Full-jitter draws each
wait from U(0, exp), halving expected schedule coverage to ≈ 30 s — most jobs exhaust
mid-outage and only 32% survive. Decorrelated's compounding waits reach further (73%).

### 3.3 Equal-jitter: coverage *and* calm

Equal-jitter keeps half of each exponential wait as a deterministic floor
(Σ floors ≈ 30 s, worst-case Σ ≈ 60 s) and randomizes only the upper half. It survived
**100% in all three runs** — matching exponential — while spreading the recovery herd
across the jitter window: peak post-recovery load 17–23 attempts/s, a ~5× reduction
vs exponential's synchronized wave. Its steady-state profile stays mid-pack
(p95 5.7 s at f = 0.5).

## 4. Discussion

**Survival is a deadline-coverage problem.** With a fixed attempt budget, what decides
outage survival is simply whether the sum of backoff waits spans the outage. Jitter
drawn from [0, exp] halves that sum in expectation — so the industry-default
full-jitter, whose purpose is protecting the *provider* from herds, quietly gives up
half the *client's* outage tolerance. Nothing about this is specific to our engine:
it falls out of arithmetic on the schedule, and the measurements land where the
arithmetic says they should (32% ≈ P(Σ of 7 uniform waits > remaining outage)).

**Jitter above a floor, not across the interval.** Equal-jitter's deterministic half
restores worst-case coverage while its random half still de-synchronizes the cohort —
in our runs it kept exponential's 100% survival *and* an ~80% smaller recovery
stampede. Where herd-smoothing matters even more, the floor can be tuned
(e.g. 0.75·exp + U(0, 0.25·exp)) — coverage degrades gracefully and predictably.

**Steady-state failure is the wrong regime to tune for.** Under uncorrelated failure,
delivery and cost are attempt-count-driven and strategy-independent; aggressive
retrying is even latency-optimal. The regime that punishes bad backoff is the
correlated one — outages — which is exactly the regime that synthetic "x% of requests
fail" testing does not exercise. Failure injection should include outage shapes.

**Practical sizing rule.** Pick the outage duration you must survive, size
Σ worst-case waits (and the attempt budget) to cover it, then add bounded jitter above
a floor. In this engine that is three environment variables — and on the strength of these
results, the engine's default strategy was switched from full-jitter to equal-jitter.

## 5. Threats to validity

- **Mock provider:** failures are synthetic and instantaneous; real providers exhibit
  latency correlated with failure, partial brown-outs, and rate limiting. The mock also
  runs in-process, so "provider load" here counts calls, not network cost.
- **Single machine:** all stacks share one host; wall-clock contention between parallel
  runs could in principle skew latency tails, though runs are alternated across slots
  evenly by strategy.
- **Scale:** N = 100 reminders × 3 repeats bounds the precision of tail percentiles
  (p99 is 3 observations deep per cell); delivery-rate differences below ~2 pp are not
  distinguishable.
- **One outage shape:** a single 60 s full outage with a fixed attempt budget; the
  survival ranking is a function of schedule-sum vs. outage length, so different
  durations move the crossover point (this is the point of §4's deadline-coverage
  framing, but we measured one instance of it).

## 6. Reproducing

```bash
./experiments/sweep.sh 3      # ~1 h on a laptop: 72 isolated runs, resumable
python3 experiments/analyze.py
```

Raw per-run JSON (including full attempt timestamp logs) is committed under
`experiments/results/raw/`.
