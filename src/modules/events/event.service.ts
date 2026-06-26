import { PrismaClient } from '@prisma/client';
import { BadRequest, UnprocessableEntity } from '../../lib/errors';
import { ReminderScheduler } from '../reminders/reminder.scheduler';

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
  constructor(
    private prisma: PrismaClient,
    private scheduler: ReminderScheduler,
  ) {}

  async createEvent(tenant: TenantCtx, input: CreateInput) {
    const startsAt = new Date(input.startsAt);
    if (Number.isNaN(startsAt.getTime())) {
      throw new BadRequest('startsAt must be a valid ISO-8601 datetime');
    }
    if (startsAt.getTime() <= Date.now()) {
      throw new UnprocessableEntity('startsAt must be in the future');
    }

    // Source of truth first: persist the event AND its PENDING reminder rows in
    // ONE transaction, so we never schedule a reminder for an event that didn't
    // commit (and never lose reminders for one that did).
    const { event, planned } = await this.prisma.$transaction(async (tx) => {
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

      // T-24h and T-2h, recorded as PENDING ReminderJob rows (future ones only).
      const planned = await this.scheduler.plan(tx, event);
      return { event, planned };
    });

    // Enqueue the BullMQ delayed jobs AFTER the commit — a queue write can't be
    // transactional with Postgres. If this throws (e.g. Redis down) we leave the
    // rows PENDING for a reconciliation sweep rather than failing the request;
    // the event itself is safely persisted (transactional-outbox pattern).
    let reminders = planned;
    try {
      reminders = await this.scheduler.enqueue(planned);
    } catch (err) {
      console.error('[reminders] enqueue failed; rows left PENDING for reconciliation:', err);
    }

    return { event, reminders };
  }
}
