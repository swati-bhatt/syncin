import type { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import { ReminderReplay } from './reminder.replay';

// Operational endpoints for the dead-letter queue. Both run inside the /v1 scope,
// so they're tenant-scoped by the same Bearer-key auth as everything else.
export default async function reminderRoutes(
  app: FastifyInstance,
  opts: { prisma: PrismaClient },
) {
  const replay = new ReminderReplay(opts.prisma);

  // Replay ALL dead-lettered reminders for the tenant (after fixing the issue).
  app.post('/reminders/replay', async (req: FastifyRequest, reply: FastifyReply) => {
    const tenant = req.tenant!;
    const ids = await replay.replayDead(tenant.id);
    return reply.code(202).send({ replayed: ids.length, reminderIds: ids });
  });

  // Re-enqueue ONE reminder (manual resend / simulate an at-least-once redelivery).
  app.post('/reminders/:id/redeliver', async (req: FastifyRequest, reply: FastifyReply) => {
    const tenant = req.tenant!;
    const { id } = req.params as { id: string };
    const result = await replay.redeliver(tenant.id, id);
    if (!result) {
      return reply.code(404).send({ error: 'not_found', message: 'reminder not found' });
    }
    return reply.code(202).send({ redelivered: result });
  });
}
