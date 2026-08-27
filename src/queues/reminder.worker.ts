import { Worker, UnrecoverableError } from 'bullmq';
import { ReminderState } from '@prisma/client';
import { redisConnection } from './connection';
import { REMINDER_QUEUE, type ReminderJobData } from './reminder.queue';
import { prisma } from '../db/prisma';
import { env } from '../config/env';
import { getProvider } from '../providers';
import { ProviderError } from '../providers/provider';
import { ReminderSender } from '../modules/reminders/reminder.sender';

// ── Retry wait-time strategies — the Paper 1 experiment axis ──
// n = attemptsMade (1-based); base/cap from env.
//   none          retry immediately (control group)
//   fixed         constant `base` wait
//   exp           capped exponential 2^(n-1)·base, no jitter — deterministic, so a
//                 cohort that failed together retries in lockstep (thundering herd)
//   full_jitter   random in [0, capped exponential] (AWS-recommended default)
//   decorrelated  min(cap, rand(base, 3·prev)) — jitter with memory of the last wait
const lastDelay = new Map<string, number>(); // decorrelated state, keyed by bull job id

export function computeBackoff(strategy: string, n: number, key: string): number {
  const base = env.REMINDER_BACKOFF_BASE_MS;
  const cap = env.REMINDER_BACKOFF_CAP_MS;
  const exp = Math.min(cap, base * 2 ** (n - 1));
  switch (strategy) {
    case 'none':
      return 0;
    case 'fixed':
      return base;
    case 'exp':
      return exp;
    case 'decorrelated': {
      const prev = lastDelay.get(key) ?? base;
      const d = Math.min(cap, base + Math.random() * Math.max(0, prev * 3 - base));
      lastDelay.set(key, d);
      return Math.floor(d);
    }
    case 'full_jitter':
    default:
      return Math.floor(Math.random() * exp);
  }
}

export function startReminderWorker() {
  const sender = new ReminderSender(prisma, getProvider());

  const worker = new Worker<ReminderJobData>(
    REMINDER_QUEUE,
    async (job) => {
      try {
        const result = await sender.send(job.data);
        if (result && ('deduped' in result || 'cancelled' in result || 'stale' in result)) {
          const why =
            'deduped' in result ? 'already-sent'
            : 'cancelled' in result ? 'event-cancelled'
            : 'stale-event';
          console.log(`[worker] SKIP (${why}) ${job.data.kind} (${job.data.reminderJobId})`);
        } else {
          console.log(`[worker] SENT ${job.data.kind} (${job.data.reminderJobId})`);
        }
      } catch (err) {
        // Permanent failures shouldn't burn retries — dead-letter them straight
        // away. Transient ones re-throw, so BullMQ retries with backoff.
        if (err instanceof ProviderError && !err.retryable) {
          throw new UnrecoverableError(err.message);
        }
        throw err;
      }
    },
    {
      connection: redisConnection.duplicate(), // workers issue blocking reads — own connection
      concurrency: 5,
      settings: {
        backoffStrategy: (attemptsMade, _type, _err, job) =>
          computeBackoff(env.REMINDER_BACKOFF_STRATEGY, attemptsMade, job?.id ?? 'anon'),
      },
    },
  );

  worker.on('completed', (job) => {
    if (job.id) lastDelay.delete(job.id); // drop decorrelated state
  });

  // Fires on EVERY failed attempt. When retries are exhausted (or the error was
  // permanent/unrecoverable), the reminder is dead-lettered: marked DEAD and left
  // in the queue's failed set (removeOnFail:false) so it can be replayed later.
  worker.on('failed', async (job, err) => {
    if (!job) return;
    const exhausted =
      err.name === 'UnrecoverableError' || job.attemptsMade >= (job.opts.attempts ?? 1);
    if (exhausted && job.id) lastDelay.delete(job.id);
    try {
      await prisma.reminderJob.update({
        where: { id: job.data.reminderJobId },
        data: {
          state: exhausted ? ReminderState.DEAD : ReminderState.FAILED,
          attempts: job.attemptsMade,
          lastError: err.message,
        },
      });
    } catch {
      // reminder row may have been deleted — ignore
    }
    console.error(
      `[worker] ${exhausted ? 'DEAD-LETTER' : 'retry'} ${job.data.kind} ` +
        `(${job.data.reminderJobId}) attempt ${job.attemptsMade}: ${err.message}`,
    );
  });

  return worker;
}
