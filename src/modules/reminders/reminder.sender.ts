import {
  PrismaClient,
  MessageDirection,
  MessageStatus,
  ReminderState,
  IdempotencyScope,
  EventStatus,
} from '@prisma/client';
import type { MessageProvider } from '../../providers/provider';
import type { ReminderJobData } from '../../queues/reminder.queue';

// Turns a fired reminder into an actual (mock) send + a message_log record.
// This is what the worker runs each time a delayed job's timer goes off.
export class ReminderSender {
  constructor(
    private prisma: PrismaClient,
    private provider: MessageProvider,
  ) {}

  async send(data: ReminderJobData) {
    const job = await this.prisma.reminderJob.findUnique({
      where: { id: data.reminderJobId },
      include: { event: { include: { recipient: true } }, tenant: true },
    });
    if (!job) return; // reminder was cancelled/deleted — nothing to do

    // The customer cancelled (e.g. replied "NO")? Don't remind about a cancelled
    // appointment — skip and mark the reminder cancelled.
    if (job.event.status === EventStatus.CANCELLED) {
      await this.prisma.reminderJob.update({
        where: { id: job.id },
        data: { state: ReminderState.CANCELLED },
      });
      return { cancelled: true };
    }

    // NO DOUBLE-SEND. A SEND idempotency key (unique on tenant+event+kind) records
    // that this reminder was already delivered. The queue is at-LEAST-once: a job
    // can run twice (e.g. the worker crashed right after sending, so BullMQ
    // re-delivers it). We check that record first and skip if it's already there.
    const alreadySent = await this.prisma.idempotencyKey.findUnique({
      where: {
        uq_idem_send: {
          tenantId: job.tenantId,
          scope: IdempotencyScope.SEND,
          eventId: job.eventId,
          kind: job.kind,
        },
      },
    });
    if (alreadySent) {
      await this.prisma.reminderJob.update({
        where: { id: job.id },
        data: { state: ReminderState.SENT },
      });
      return { deduped: true };
    }

    await this.prisma.reminderJob.update({
      where: { id: job.id },
      data: { state: ReminderState.SENDING },
    });

    const body = this.formatMessage(job);

    // Pass idempotencyKey to the provider too: a real provider (Twilio) dedupes on
    // it, covering the tiny window between a successful send and our commit below.
    const result = await this.provider.sendMessage({
      to: job.event.recipient.phone,
      body,
      idempotencyKey: job.id,
    });

    // ONLY on success: record the message, claim the SEND key, and mark SENT — all
    // atomically. On failure the send threw above and no key is written, so a retry
    // is free to try again.
    await this.prisma.$transaction([
      this.prisma.messageLog.create({
        data: {
          tenantId: job.tenantId,
          eventId: job.eventId,
          reminderJobId: job.id,
          direction: MessageDirection.OUTBOUND,
          providerMsgId: result.providerMsgId,
          status: MessageStatus.SENT,
          body,
        },
      }),
      this.prisma.idempotencyKey.create({
        data: {
          tenantId: job.tenantId,
          scope: IdempotencyScope.SEND,
          eventId: job.eventId,
          kind: job.kind,
        },
      }),
      this.prisma.reminderJob.update({
        where: { id: job.id },
        data: { state: ReminderState.SENT },
      }),
    ]);

    return result;
  }

  // The tenant's timezone finally matters here: the message says the LOCAL
  // wall-clock time of the appointment.
  private formatMessage(job: {
    kind: string;
    event: { startsAt: Date; recipient: { name: string } };
    tenant: { name: string; timezone: string };
  }) {
    const when = new Intl.DateTimeFormat('en-US', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: job.tenant.timezone,
    }).format(job.event.startsAt);
    const lead = job.kind === 'T24H' ? 'tomorrow' : 'in about 2 hours';
    return `Hi ${job.event.recipient.name}, reminder: your appointment with ${job.tenant.name} is ${lead} — ${when}. Reply YES to confirm.`;
  }
}
