import Fastify from 'fastify';
import { env } from './config/env';
import { prisma } from './db/prisma';
import { createProvider } from './providers';
import { tenantHook } from './plugins/tenant';
import eventRoutes from './modules/events/event.routes';
import { redisConnection } from './queues/connection';
import { AppError } from './lib/errors';

export async function buildApp() {
  const app = Fastify({ logger: { level: env.LOG_LEVEL } });

  app.decorateRequest('tenant', null);

  // Central error mapping: AppError → its status/code; schema failures → 400;
  // anything else → opaque 500 (never leak internals).
  app.setErrorHandler((err, req, reply) => {
    if (err instanceof AppError) {
      return reply.code(err.statusCode).send({ error: err.code, message: err.message });
    }
    if ((err as { validation?: unknown }).validation) {
      return reply.code(400).send({ error: 'validation_error', message: err.message });
    }
    req.log.error(err);
    return reply.code(500).send({ error: 'internal_error', message: 'Something went wrong' });
  });

  // Unauthenticated ops endpoints (outside the tenant scope).
  app.get('/health', async () => ({ status: 'ok' }));
  app.get('/ready', async () => {
    await prisma.$queryRaw`SELECT 1`;
    await redisConnection.ping();
    return { status: 'ready' };
  });

  const provider = createProvider();
  app.log.info(`message provider = ${provider.name}`);

  // Everything under /v1 requires a valid tenant API key.
  await app.register(
    async (v1) => {
      v1.addHook('onRequest', tenantHook(prisma));
      await v1.register(eventRoutes, { prisma, provider });
    },
    { prefix: '/v1' },
  );

  return app;
}
