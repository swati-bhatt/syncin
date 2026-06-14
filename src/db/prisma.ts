import { PrismaClient } from '@prisma/client';
import { env } from '../config/env';

// Single shared PrismaClient for the process.
export const prisma = new PrismaClient({
  log: env.LOG_LEVEL === 'debug' ? ['query', 'warn', 'error'] : ['warn', 'error'],
});
