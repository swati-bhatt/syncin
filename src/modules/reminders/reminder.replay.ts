import { PrismaClient, ReminderKind, ReminderState } from '@prisma/client';
import { reminderQueue, type ReminderJobData } from '../../queues/reminder.queue';
import { env } from '../../config/env';

// Re-driving reminders: replay dead-lettered ones (after the underlying problem is
// fixed) and manually redeliver a single reminder.
export class ReminderReplay {
  constructor(private prisma: PrismaClient) {}

  // Re-enqueue ONE reminder immediately. No jobId is set, so BullMQ assigns a fresh
  // one — meaning a previously completed/failed job id won't dedupe it away. Used to
  // (a) manually re-send and (b) simulate an at-least-once redelivery in tests.
  async redeliver(tenantId: string, reminderId: string) {
    const r = await this.prisma.reminderJob.findFirst({ where: { id: reminderId, tenantId } });
    if (!r) return null;
    await this.enqueueNow(r);
    return r.id;
  }

  // Replay every dead-lettered reminder for a tenant: reset state + re-enqueue.
  async replayDead(tenantId: string) {
    const dead = await this.prisma.reminderJob.findMany({
      where: { tenantId, state: ReminderState.DEAD },
    });
    for (const r of dead) {
      await this.prisma.reminderJob.update({
        where: { id: r.id },
        data: { state: ReminderState.SCHEDULED, attempts: 0, lastError: null },
      });
      await this.enqueueNow(r);
    }
    return dead.map((d) => d.id);
  }

  private async enqueueNow(r: { id: string; eventId: string; tenantId: string; kind: ReminderKind }) {
    const data: ReminderJobData = {
      reminderJobId: r.id,
      eventId: r.eventId,
      tenantId: r.tenantId,
      kind: r.kind,
    };
    await reminderQueue.add(r.kind, data, {
      attempts: env.REMINDER_MAX_ATTEMPTS,
      backoff: { type: 'custom' },
      removeOnComplete: true,
      removeOnFail: false,
    });
  }
}
