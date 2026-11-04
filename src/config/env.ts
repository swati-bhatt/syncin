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
  // Outage coverage scales with the attempt budget: with these defaults (5 attempts,
  // base 1 s, cap 15 s) equal jitter's 4 waits reach only 7.5–15 s; the study's outage
  // runs used 8 attempts. Size attempts/base/cap to the outage you must survive.
  REMINDER_MAX_ATTEMPTS: Number(process.env.REMINDER_MAX_ATTEMPTS ?? 5),
  REMINDER_BACKOFF_BASE_MS: Number(process.env.REMINDER_BACKOFF_BASE_MS ?? 1000),
  REMINDER_BACKOFF_CAP_MS: Number(process.env.REMINDER_BACKOFF_CAP_MS ?? 15000),
  // none | fixed | exp | full_jitter | equal_jitter | decorrelated — see reminder.worker.ts
  // (unknown names fall back to full_jitter)
  REMINDER_BACKOFF_STRATEGY: process.env.REMINDER_BACKOFF_STRATEGY ?? 'equal_jitter', // default chosen by the measurement study (experiments/REPORT.md)

  // Mock-provider failure-injection rig (Milestone 6). Knobs for experiments;
  // all default to "off" so normal runs behave like a reliable provider.
  MOCK_FAILURE_RATE: Number(process.env.MOCK_FAILURE_RATE ?? 0), // 0..1 chance a send fails
  MOCK_PERMANENT_RATE: Number(process.env.MOCK_PERMANENT_RATE ?? 0), // 0..1 of failures that are permanent
  MOCK_LATENCY_MS: Number(process.env.MOCK_LATENCY_MS ?? 0), // delay added to every send
  MOCK_RECOVER_AFTER_MS: Number(process.env.MOCK_RECOVER_AFTER_MS ?? 0), // outage that clears after N ms of process uptime (counted from boot, not from when reminders come due)
};
