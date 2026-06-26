import { buildApp } from './app';
import { env } from './config/env';
import { prisma } from './db/prisma';
import { reminderQueue } from './queues/reminder.queue';
import { redisConnection } from './queues/connection';

const app = await buildApp();

try {
  await app.listen({ port: env.PORT, host: env.HOST });
} catch (err) {
  app.log.error(err);
  process.exit(1);
}

// Graceful shutdown: stop accepting connections, finish in-flight requests, then
// close the queue, Redis, and DB pool. (The BullMQ worker gets its own drain in M3.)
async function shutdown(signal: string) {
  app.log.info({ signal }, 'shutting down');
  try {
    await app.close();
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
