import { PrismaClient } from '@prisma/client';
import { BadRequest, UnprocessableEntity } from '../../lib/errors';

interface CreateInput {
  recipient: { name: string; phone: string };
  startsAt: string;
  notes?: string;
  memberId?: string;
}

interface TenantCtx {
  id: string;
  name: string;
  timezone: string;
}

export class EventService {
  constructor(private prisma: PrismaClient) {}

  async createEvent(tenant: TenantCtx, input: CreateInput) {
    const startsAt = new Date(input.startsAt);
    if (Number.isNaN(startsAt.getTime())) {
      throw new BadRequest('startsAt must be a valid ISO-8601 datetime');
    }
    if (startsAt.getTime() <= Date.now()) {
      throw new UnprocessableEntity('startsAt must be in the future');
    }

    return this.prisma.$transaction(async (tx) => {
      // Find-or-create the recipient WITHIN this tenant (phone is unique per tenant).
      const recipient = await tx.recipient.upsert({
        where: { tenantId_phone: { tenantId: tenant.id, phone: input.recipient.phone } },
        update: { name: input.recipient.name },
        create: {
          tenantId: tenant.id,
          name: input.recipient.name,
          phone: input.recipient.phone,
        },
      });

      const event = await tx.event.create({
        data: {
          tenantId: tenant.id, // ← every write carries the tenant id
          recipientId: recipient.id,
          memberId: input.memberId ?? null,
          startsAt,
          notes: input.notes ?? null,
        },
        include: { recipient: true },
      });

      // ───────────────────────── Milestone 2 seam ─────────────────────────
      // Compute reminder fire-times in tenant.timezone:
      //     T-24h = startsAt − 24h,  T-2h = startsAt − 2h
      // then enqueue two BullMQ DELAYED jobs and write a ReminderJob row
      // (state=SCHEDULED, bullJobId) per kind. The @@unique([eventId, kind])
      // on reminder_jobs is the scheduling-idempotency guard.
      //
      // Enqueue happens AFTER commit (a queue write can't be transactional with
      // Postgres); a periodic reconciliation sweep re-enqueues any ReminderJob
      // left in PENDING, so a crash between commit and enqueue self-heals
      // instead of silently losing a reminder (outbox pattern).
      //
      // Milestone 1 only persists the event.
      return event;
    });
  }
}
