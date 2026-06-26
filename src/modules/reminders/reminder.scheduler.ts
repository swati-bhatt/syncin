import { Prisma, PrismaClient, ReminderKind, ReminderState, ReminderJob } from '@prisma/client';
import { reminderQueue, type ReminderJobData } from '../../queues/reminder.queue';

// Reminder fire-times are fixed offsets before the event's absolute start instant,
// so they're timezone-independent (T-24h before an instant is that instant minus
// 24h in every zone). tenant.timezone only matters for wall-clock phrasing in the
// message body, which happens when we actually send (M3).
const OFFSET_MS: Record<ReminderKind, number> = {
  T24H: 24 * 60 * 60 * 1000,
  T2H: 2 * 60 * 60 * 1000,
};

export class ReminderScheduler {
  constructor(private prisma: PrismaClient) {}

  // INSIDE the create transaction: persist a PENDING ReminderJob for each kind
  // whose fire-time is still in the future. Atomic with the event insert, and the
  // @@unique([eventId, kind]) guard means a retried create can't double-schedule.
  async plan(
    tx: Prisma.TransactionClient,
    event: { id: string; tenantId: string; startsAt: Date },
  ): Promise<ReminderJob[]> {
    const now = Date.now();
    const planned: ReminderJob[] = [];
    for (const kind of [ReminderKind.T24H, ReminderKind.T2H]) {
      const runAt = new Date(event.startsAt.getTime() - OFFSET_MS[kind]);
      if (runAt.getTime() <= now) continue; // never schedule a reminder in the past
      planned.push(
        await tx.reminderJob.create({
          data: {
            tenantId: event.tenantId,
            eventId: event.id,
            kind,
            runAt,
            state: ReminderState.PENDING,
          },
        }),
      );
    }
    return planned;
  }

  // AFTER commit: enqueue one DELAYED BullMQ job per planned reminder, then flip
  // the row to SCHEDULED. jobId = ReminderJob.id makes the enqueue itself
  // idempotent (re-adding an existing jobId is a no-op); the PENDING rows are the
  // outbox safety net if this step never runs.
  async enqueue(planned: ReminderJob[]): Promise<ReminderJob[]> {
    const out: ReminderJob[] = [];
    for (const r of planned) {
      const delay = Math.max(0, r.runAt.getTime() - Date.now());
      const data: ReminderJobData = {
        reminderJobId: r.id,
        eventId: r.eventId,
        tenantId: r.tenantId,
        kind: r.kind,
      };
      const job = await reminderQueue.add(r.kind, data, {
        delay,
        jobId: r.id,
        removeOnComplete: true,
        removeOnFail: false,
      });
      out.push(
        await this.prisma.reminderJob.update({
          where: { id: r.id },
          data: { state: ReminderState.SCHEDULED, bullJobId: job.id ?? null },
        }),
      );
    }
    return out;
  }
}
