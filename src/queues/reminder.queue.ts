import { Queue } from 'bullmq';
import { redisConnection } from './connection';

export const REMINDER_QUEUE = 'reminders';

// Payload carried by each delayed job. The worker (M3) will load the ReminderJob
// row by id, then its event + recipient, and send via the Provider.
export interface ReminderJobData {
  reminderJobId: string;
  eventId: string;
  tenantId: string;
  kind: 'T24H' | 'T2H';
}

export const reminderQueue = new Queue<ReminderJobData>(REMINDER_QUEUE, {
  connection: redisConnection,
});
