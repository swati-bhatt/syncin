import { buildApp } from './app';
import { env } from './config/env';
import { prisma } from './db/prisma';
import { reminderQueue } from './queues/reminder.queue';
import { redisConnection } from './queues/connection';
import { startReminderWorker } from './queues/reminder.worker';

const app = await buildApp();

try {
  await app.listen({ port: env.PORT, host: env.HOST });
} catch (err) {
  app.log.error(err);
  process.exit(1);
}

// Start the background worker that delivers reminders when their timers fire.
const reminderWorker = startReminderWorker();
app.log.info('reminder worker started');

// Graceful shutdown: stop accepting requests, drain the worker (finish in-flight
// sends), then close the queue, Redis, and DB pool — in that order.
async function shutdown(signal: string) {
  app.log.info({ signal }, 'shutting down');
  try {
    await app.close();
    await reminderWorker.close(); // stops taking new jobs, lets in-flight finish
    await reminderQueue.close();
    await redisConnection.quit();
    await prisma.$disconnect();
    process.exit(0);
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}

for (const sig of ['SIGTERM', 'SIGINT'] as const) {
  process.on(sig, () => void shutdown(sig));
}
