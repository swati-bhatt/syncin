import { Worker } from 'bullmq';
import { redisConnection } from './connection';
import { REMINDER_QUEUE, type ReminderJobData } from './reminder.queue';
import { prisma } from '../db/prisma';
import { createProvider } from '../providers';
import { ReminderSender } from '../modules/reminders/reminder.sender';

// Starts the background worker that consumes the reminders queue. When a delayed
// job's timer fires, BullMQ hands the job here and we send + log it. `concurrency`
// is how many reminders this worker will process at once.
export function startReminderWorker() {
  const sender = new ReminderSender(prisma, createProvider());

  const worker = new Worker<ReminderJobData>(
    REMINDER_QUEUE,
    async (job) => {
      await sender.send(job.data);
    },
    // Its own Redis connection (workers issue blocking reads — don't share the
    // queue's connection).
    { connection: redisConnection.duplicate(), concurrency: 5 },
  );

  worker.on('completed', (job) =>
    console.log(`[worker] sent ${job.data.kind} reminder (${job.data.reminderJobId})`),
  );
  worker.on('failed', (job, err) =>
    console.error(`[worker] reminder ${job?.data?.reminderJobId} failed: ${err.message}`),
  );

  return worker;
}
