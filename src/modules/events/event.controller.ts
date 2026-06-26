import type { FastifyReply, FastifyRequest } from 'fastify';
import type { PrismaClient, ReminderJob } from '@prisma/client';
import type { MessageProvider } from '../../providers/provider';
import { EventService } from './event.service';
import { ReminderScheduler } from '../reminders/reminder.scheduler';
import { IdempotencyService } from '../../lib/idempotency';

interface CreateBody {
  recipient: { name: string; phone: string };
  startsAt: string;
  notes?: string;
  memberId?: string;
}

function serializeEvent(
  e: {
    id: string;
    status: string;
    startsAt: Date;
    notes: string | null;
    createdAt: Date;
    recipient?: { id: string; name: string; phone: string } | null;
  },
  reminders: ReminderJob[],
) {
  return {
    id: e.id,
    status: e.status,
    startsAt: e.startsAt.toISOString(),
    notes: e.notes,
    recipient: e.recipient
      ? { id: e.recipient.id, name: e.recipient.name, phone: e.recipient.phone }
      : undefined,
    reminders: reminders.map((r) => ({
      kind: r.kind,
      runAt: r.runAt.toISOString(),
      state: r.state,
    })),
    createdAt: e.createdAt.toISOString(),
  };
}

export function eventController(deps: { prisma: PrismaClient; provider: MessageProvider }) {
  const service = new EventService(deps.prisma, new ReminderScheduler(deps.prisma));
  const idempotency = new IdempotencyService(deps.prisma);

  return {
    create: async (req: FastifyRequest, reply: FastifyReply) => {
      const tenant = req.tenant!; // guaranteed by the tenant hook
      const idemKey =
        (req.headers['idempotency-key'] as string | undefined)?.trim() || undefined;

      // Reserve-first idempotency: claim the key before doing the work.
      if (idemKey) {
        const r = await idempotency.reserve(tenant.id, idemKey);
        if (!r.reserved) {
          if (r.existing?.responseStatus && r.existing.responseBody) {
            // First request already finished → replay its exact response.
            return reply.code(r.existing.responseStatus).send(r.existing.responseBody);
          }
          // First request is still in flight (reserved, not yet completed).
          return reply.code(409).send({
            error: 'request_in_progress',
            message: 'A request with this Idempotency-Key is already being processed.',
          });
        }
      }

      const { event, reminders } = await service.createEvent(tenant, req.body as CreateBody);
      const body = serializeEvent(event, reminders);

      if (idemKey) await idempotency.complete(tenant.id, idemKey, 201, body);

      return reply.code(201).send(body);
    },
  };
}
