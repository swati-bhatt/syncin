import type { FastifyReply, FastifyRequest } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import { Unauthorized } from '../lib/errors';

// Augment the request with the resolved tenant. Set by the onRequest hook below
// and read by every handler in the authenticated (/v1) scope.
declare module 'fastify' {
  interface FastifyRequest {
    tenant: { id: string; name: string; timezone: string } | null;
  }
}

// Resolve the tenant from the Bearer API key. The tenant_id is DERIVED from a
// verified secret — never taken from a header/body the client controls.
export function tenantHook(prisma: PrismaClient) {
  return async function (req: FastifyRequest, _reply: FastifyReply) {
    const auth = req.headers['authorization'];
    if (!auth || !auth.startsWith('Bearer ')) {
      throw new Unauthorized('Missing Bearer API key');
    }

    const apiKey = auth.slice('Bearer '.length).trim();
    const tenant = await prisma.tenant.findUnique({
      where: { apiKey },
      select: { id: true, name: true, timezone: true },
    });

    if (!tenant) throw new Unauthorized('Invalid API key');
    req.tenant = tenant;
  };
}
