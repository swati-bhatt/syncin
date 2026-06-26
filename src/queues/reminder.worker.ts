import { Worker, UnrecoverableError } from 'bullmq';
import { ReminderState } from '@prisma/client';
import { redisConnection } from './connection';
import { REMINDER_QUEUE, type ReminderJobData } from './reminder.queue';
import { prisma } from '../db/prisma';
import { env } from '../config/env';
import { createProvider } from '../providers';
import { ProviderError } from '../providers/provider';
import { ReminderSender } from '../modules/reminders/reminder.sender';

// Exponential backoff with FULL JITTER: wait a random time in [0, capped 2^n].
// Jitter matters because a fleet of reminders that all failed at the same instant
// would otherwise retry in lockstep and stampede a recovering provider. Spreading
// them randomly smooths the load. (Paper 1 compares this vs plain exponential.)
function fullJitterBackoff(attemptsMade: number): number {
  const exponential = env.REMINDER_BACKOFF_BASE_MS * 2 ** (attemptsMade - 1);
  const capped = Math.min(env.REMINDER_BACKOFF_CAP_MS, exponential);
  return Math.floor(Math.random() * capped);
}

export function startReminderWorker() {
  const sender = new ReminderSender(prisma, createProvider());

  const worker = new Worker<ReminderJobData>(
    REMINDER_QUEUE,
    async (job) => {
      try {
        const result = await sender.send(job.data);
        if (result && 'deduped' in result) {
          console.log(`[worker] SKIP already-sent ${job.data.kind} (${job.data.reminderJobId})`);
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
      settings: { backoffStrategy: (attemptsMade) => fullJitterBackoff(attemptsMade) },
    },
  );

  // (SENT / SKIP is logged inside the processor above.)

  // Fires on EVERY failed attempt. When retries are exhausted (or the error was
  // permanent/unrecoverable), the reminder is dead-lettered: marked DEAD and left
  // in the queue's failed set (removeOnFail:false) so it can be replayed later.
  worker.on('failed', async (job, err) => {
    if (!job) return;
    const exhausted =
      err.name === 'UnrecoverableError' || job.attemptsMade >= (job.opts.attempts ?? 1);
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
