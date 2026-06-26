import {
  PrismaClient,
  Prisma,
  EventStatus,
  MessageDirection,
  MessageStatus,
  IdempotencyScope,
} from '@prisma/client';

export interface InboundMessage {
  providerMsgId: string;
  from: string; // E.164
  body: string;
}

// Handles an inbound provider webhook (a customer's reply). At-least-once like
// everything else — providers re-deliver webhooks — so we dedupe on the inbound
// message id (INBOUND idempotency scope) before doing any work.
export class InboundService {
  constructor(private prisma: PrismaClient) {}

  async handle(tenantId: string, msg: InboundMessage) {
    // 1. Dedupe by provider message id.
    const fresh = await this.claimInbound(tenantId, msg.providerMsgId);
    if (!fresh) return { result: 'duplicate' as const };

    // 2. Match the sender to a recipient within this tenant.
    const recipient = await this.prisma.recipient.findUnique({
      where: { tenantId_phone: { tenantId, phone: msg.from } },
    });

    // 3. Their nearest upcoming, still-scheduled appointment (if any).
    const event = recipient
      ? await this.prisma.event.findFirst({
          where: {
            tenantId,
            recipientId: recipient.id,
            status: EventStatus.SCHEDULED,
            startsAt: { gt: new Date() },
          },
          orderBy: { startsAt: 'asc' },
        })
      : null;

    // 4. Log the inbound message (linked to the event when we matched one).
    await this.prisma.messageLog.create({
      data: {
        tenantId,
        eventId: event?.id ?? null,
        direction: MessageDirection.INBOUND,
        providerMsgId: msg.providerMsgId,
        status: MessageStatus.RECEIVED,
        body: msg.body,
      },
    });

    if (!recipient) return { result: 'no_recipient' as const };
    if (!event) return { result: 'no_upcoming_event' as const };

    // 5. Interpret the reply and update the appointment.
    const reply = msg.body.trim().toUpperCase();
    let newStatus: EventStatus | null = null;
    if (['YES', 'Y', 'CONFIRM', 'C'].includes(reply)) newStatus = EventStatus.CONFIRMED;
    else if (['NO', 'N', 'CANCEL', 'X'].includes(reply)) newStatus = EventStatus.CANCELLED;

    if (!newStatus) return { result: 'unrecognized' as const, eventId: event.id };

    await this.prisma.event.update({ where: { id: event.id }, data: { status: newStatus } });
    return { result: 'updated' as const, eventId: event.id, status: newStatus };
  }

  // Reserve-first INBOUND idempotency key (unique on tenant + provider_msg_id).
  private async claimInbound(tenantId: string, providerMsgId: string) {
    try {
      await this.prisma.idempotencyKey.create({
        data: { tenantId, scope: IdempotencyScope.INBOUND, providerMsgId },
      });
      return true;
    } catch (e) {
      if (e instanceof Prisma.PrismaClientKnownRequestError && e.code === 'P2002') return false;
      throw e;
    }
  }
}
