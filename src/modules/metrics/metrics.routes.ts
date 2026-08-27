import type { FastifyInstance } from 'fastify';
import type { PrismaClient } from '@prisma/client';
import { MetricsService } from './metrics.service';

// GET /metrics — engine-wide metrics, no auth (like /health). In production this
// would sit behind network controls and/or be emitted in Prometheus text format.
// GET /metrics/attempts — raw per-attempt provider log (timestamp, ok, key) for
// the measurement study's latency/herd analysis.
export default async function metricsRoutes(
  app: FastifyInstance,
  opts: { prisma: PrismaClient },
) {
  const service = new MetricsService(opts.prisma);
  app.get('/metrics', async () => service.gather());
  app.get('/metrics/attempts', async () => ({ attempts: service.providerAttempts() }));
}
