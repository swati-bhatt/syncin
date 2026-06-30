import { PrismaClient, ReminderState } from '@prisma/client';
import { reminderQueue } from '../../queues/reminder.queue';

// Aggregated engine metrics for experiments + monitoring: queue depth, reminder
// states, retry volume, and dead-letter size. (The numbers Paper 1 records.)
export class MetricsService {
  constructor(private prisma: PrismaClient) {}

  async gather() {
    const [queue, states, attempts, messages] = await Promise.all([
      reminderQueue.getJobCounts('waiting', 'active', 'delayed', 'completed', 'failed'),
      this.prisma.reminderJob.groupBy({ by: ['state'], _count: { _all: true } }),
      this.prisma.reminderJob.aggregate({ _sum: { attempts: true } }),
      this.prisma.messageLog.groupBy({ by: ['direction', 'status'], _count: { _all: true } }),
    ]);

    const byState: Record<string, number> = {};
    for (const s of states) byState[s.state] = s._count._all;

    return {
      queue, // waiting · active · delayed · completed · failed (failed = dead-letter set size)
      reminders: {
        byState,
        deadLetter: byState[ReminderState.DEAD] ?? 0,
        totalAttempts: attempts._sum.attempts ?? 0,
      },
      messages: messages.map((m) => ({
        direction: m.direction,
        status: m.status,
        count: m._count._all,
      })),
    };
  }
}
