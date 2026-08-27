import { randomUUID } from 'node:crypto';
import {
  ProviderError,
  type MessageProvider,
  type SendMessageInput,
  type SendMessageResult,
} from './provider';
import { env } from '../config/env';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface ProviderAttempt {
  t: number; // Date.now() at the attempt — herd-effect analysis needs raw timestamps
  ok: boolean;
  key: string; // idempotencyKey (the reminder id)
}

// The default provider + the failure-injection rig. Two layers:
//
//  (1) DETERMINISTIC name triggers (tests/demos) — recipient name contains:
//        "flaky"    -> transient failure on attempts 1-2, succeeds on the 3rd
//        "doomed"   -> transient failure that never recovers (-> DLQ)
//        "broken"   -> permanent, non-retryable failure (-> DLQ immediately)
//        "comeback" -> fails the whole first run, succeeds on replay
//
//  (2) PROBABILISTIC injection (experiments / Paper 1), all via env:
//        MOCK_LATENCY_MS        - delay added to every send
//        MOCK_RECOVER_AFTER_MS  - outage: fail everything until uptime > N ms
//        MOCK_FAILURE_RATE      - probability (0..1) a send fails
//        MOCK_PERMANENT_RATE    - of those failures, the share that are permanent
//
// Every attempt (success or failure) is recorded with a timestamp — that log is
// what the measurement study reads back through /metrics/attempts.
export class MockProvider implements MessageProvider {
  readonly name = 'mock';
  private calls = new Map<string, number>(); // attempts seen per idempotencyKey
  private attempts: ProviderAttempt[] = [];
  private counters = { calls: 0, ok: 0, failed: 0 };

  stats() {
    return { name: this.name, ...this.counters, uniqueKeys: this.calls.size };
  }

  attemptLog(): ProviderAttempt[] {
    return this.attempts;
  }

  private record(ok: boolean, key: string) {
    this.counters.calls += 1;
    if (ok) this.counters.ok += 1;
    else this.counters.failed += 1;
    if (this.attempts.length < 50000) this.attempts.push({ t: Date.now(), ok, key });
  }

  async sendMessage(input: SendMessageInput): Promise<SendMessageResult> {
    if (env.MOCK_LATENCY_MS > 0) await sleep(env.MOCK_LATENCY_MS);

    const n = (this.calls.get(input.idempotencyKey) ?? 0) + 1;
    this.calls.set(input.idempotencyKey, n);
    const body = input.body.toLowerCase();

    const fail = (msg: string, retryable: boolean): never => {
      this.record(false, input.idempotencyKey);
      throw new ProviderError(msg, retryable);
    };

    // --- deterministic name triggers ---
    if (body.includes('broken')) fail('mock: permanent failure (unreachable number)', false);
    if (body.includes('doomed')) fail(`mock: transient failure (attempt ${n})`, true);
    if (body.includes('comeback') && n <= env.REMINDER_MAX_ATTEMPTS)
      fail(`mock: transient failure (attempt ${n})`, true);
    if (body.includes('flaky') && n < 3) fail(`mock: transient failure (attempt ${n})`, true);

    // --- outage that recovers at time T ---
    if (env.MOCK_RECOVER_AFTER_MS > 0 && process.uptime() * 1000 < env.MOCK_RECOVER_AFTER_MS)
      fail('mock: provider outage', true);

    // --- probabilistic failure injection ---
    if (env.MOCK_FAILURE_RATE > 0 && Math.random() < env.MOCK_FAILURE_RATE) {
      const permanent = Math.random() < env.MOCK_PERMANENT_RATE;
      fail(`mock: injected ${permanent ? 'permanent' : 'transient'} failure`, !permanent);
    }

    this.record(true, input.idempotencyKey);
    return { providerMsgId: `mock_${randomUUID()}`, status: 'SENT' };
  }
}
