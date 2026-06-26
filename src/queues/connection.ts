import { Redis } from 'ioredis';
import { env } from '../config/env';

// Shared Redis connection for BullMQ. `maxRetriesPerRequest: null` is required by
// BullMQ — it issues blocking commands that must not be aborted by ioredis.
export const redisConnection = new Redis(env.REDIS_URL, {
  maxRetriesPerRequest: null,
});
