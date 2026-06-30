import type { FastifyInstance } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import { MetricsService } from './metrics.service';

// GET /metrics — engine-wide metrics, no auth (like /health). In production this
// would sit behind network controls and/or be emitted in Prometheus text format.
export default async function metricsRoutes(
  app: FastifyInstance,
  opts: { prisma: PrismaClient },
) {
  const service = new MetricsService(opts.prisma);
  app.get('/metrics', async () => service.gather());
}
