function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

export const env = {
  DATABASE_URL: required('DATABASE_URL'),
  REDIS_URL: process.env.REDIS_URL ?? 'redis://localhost:6379',
  PORT: Number(process.env.PORT ?? 3000),
  HOST: process.env.HOST ?? '0.0.0.0',
  PROVIDER: (process.env.PROVIDER ?? 'mock') as 'mock' | 'twilio',
  LOG_LEVEL: process.env.LOG_LEVEL ?? 'info',

  // Reminder delivery retry policy (Milestone 4). Tunable — Paper 1 sweeps these.
  REMINDER_MAX_ATTEMPTS: Number(process.env.REMINDER_MAX_ATTEMPTS ?? 5),
  REMINDER_BACKOFF_BASE_MS: Number(process.env.REMINDER_BACKOFF_BASE_MS ?? 1000),
  REMINDER_BACKOFF_CAP_MS: Number(process.env.REMINDER_BACKOFF_CAP_MS ?? 15000),
};
