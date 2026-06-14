import type { FastifyInstance } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import type { MessageProvider } from '../../providers/provider';
import { eventController } from './event.controller';
import { createEventSchema } from './event.schema';

// routes → controller → service → data. This plugin only wires HTTP; all
// business logic lives in the service.
export default async function eventRoutes(
  app: FastifyInstance,
  opts: { prisma: PrismaClient; provider: MessageProvider },
) {
  const controller = eventController({
    prisma: opts.prisma,
    provider: opts.provider,
  });

  app.post('/events', { schema: createEventSchema }, controller.create);
}
