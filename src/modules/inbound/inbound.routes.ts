import type { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import { InboundService, type InboundMessage } from './inbound.service';

const inboundSchema = {
  params: {
    type: 'object',
    required: ['tenantId'],
    properties: { tenantId: { type: 'string', format: 'uuid' } },
  },
  body: {
    type: 'object',
    required: ['providerMsgId', 'from', 'body'],
    additionalProperties: false,
    properties: {
      providerMsgId: { type: 'string', minLength: 1, maxLength: 255 },
      from: { type: 'string', pattern: '^\\+[1-9]\\d{6,14}$' }, // E.164
      body: { type: 'string', minLength: 1, maxLength: 1000 },
    },
  },
} as const;

// Inbound provider webhooks. Called by the messaging provider, NOT a tenant — so
// there's no Bearer auth; the tenant is in the path. (Production: verify the
// provider's request signature here, e.g. Twilio's X-Twilio-Signature.)
export default async function inboundRoutes(
  app: FastifyInstance,
  opts: { prisma: PrismaClient },
) {
  const service = new InboundService(opts.prisma);

  app.post(
    '/webhooks/:tenantId/inbound',
    { schema: inboundSchema },
    async (req: FastifyRequest, reply: FastifyReply) => {
      const { tenantId } = req.params as { tenantId: string };

      const tenant = await opts.prisma.tenant.findUnique({
        where: { id: tenantId },
        select: { id: true },
      });
      if (!tenant) return reply.code(404).send({ error: 'tenant_not_found' });

      const outcome = await service.handle(tenantId, req.body as InboundMessage);
      // Always 200 to the provider (even on "no match") so it doesn't retry forever.
      return reply.code(200).send(outcome);
    },
  );
}
