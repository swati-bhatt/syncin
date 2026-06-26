import {
  PrismaClient,
  MessageDirection,
  MessageStatus,
  ReminderState,
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

    await this.prisma.reminderJob.update({
      where: { id: job.id },
      data: { state: ReminderState.SENDING, attempts: { increment: 1 } },
    });

    const body = this.formatMessage(job);

    const result = await this.provider.sendMessage({
      to: job.event.recipient.phone,
      body,
      idempotencyKey: job.id, // M4 will use this provider-side to dedupe at-least-once delivery
    });

    // Record the send and mark the reminder done — atomically.
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
