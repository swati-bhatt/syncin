import { Prisma, PrismaClient, IdempotencyScope } from '@prisma/client';

// API-request idempotency, Stripe-style. The key insight is RESERVE-FIRST:
// we insert the idempotency row BEFORE doing the work, so the unique constraint
// — not a check-then-act race — is what guarantees exactly-once creation under
// concurrent retries.
export class IdempotencyService {
  constructor(private prisma: PrismaClient) {}

  // Try to claim the key. If it already exists, return what we know about it.
  async reserve(tenantId: string, requestKey: string) {
    try {
      await this.prisma.idempotencyKey.create({
        data: { tenantId, scope: IdempotencyScope.API_REQUEST, requestKey },
      });
      return { reserved: true as const };
    } catch (e) {
      if (e instanceof Prisma.PrismaClientKnownRequestError && e.code === 'P2002') {
        const existing = await this.prisma.idempotencyKey.findUnique({
          where: {
            uq_idem_api: { tenantId, scope: IdempotencyScope.API_REQUEST, requestKey },
          },
        });
        return { reserved: false as const, existing };
      }
      throw e;
    }
  }

  // Persist the response so a later retry with the same key replays it verbatim.
  async complete(tenantId: string, requestKey: string, status: number, body: unknown) {
    await this.prisma.idempotencyKey.update({
      where: { uq_idem_api: { tenantId, scope: IdempotencyScope.API_REQUEST, requestKey } },
      data: { responseStatus: status, responseBody: body as Prisma.InputJsonValue },
    });
  }

  // NOTE (follow-up): if the handler crashes AFTER reserve() but BEFORE
  // complete(), the row is stuck "in progress" and blocks that key forever.
  // A periodic sweep should expire reserved-but-incomplete rows older than N
  // minutes. Out of scope for M1.
}
