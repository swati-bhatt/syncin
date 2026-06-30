import { randomUUID } from 'node:crypto';
import {
  ProviderError,
  type MessageProvider,
  type SendMessageInput,
  type SendMessageResult,
} from './provider';
import { env } from '../config/env';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// The default provider + the Milestone 6 failure-injection rig. Two layers:
//
//  (1) DETERMINISTIC name triggers (for tests/demos) — recipient name contains:
//        "flaky"    -> transient failure on attempts 1-2, succeeds on the 3rd
//        "doomed"   -> transient failure that never recovers (-> DLQ)
//        "broken"   -> permanent, non-retryable failure (-> DLQ immediately)
//        "comeback" -> fails the whole first run, succeeds on replay
//
//  (2) PROBABILISTIC injection (for experiments / Paper 1), all via env:
//        MOCK_LATENCY_MS        - delay added to every send
//        MOCK_RECOVER_AFTER_MS  - simulate an outage: fail everything until the
//                                 process has been up this long, then recover
//        MOCK_FAILURE_RATE      - probability (0..1) a send fails
//        MOCK_PERMANENT_RATE    - of those failures, the share that are permanent
export class MockProvider implements MessageProvider {
  readonly name = 'mock';
  private calls = new Map<string, number>(); // attempts seen per idempotencyKey

  async sendMessage(input: SendMessageInput): Promise<SendMessageResult> {
    if (env.MOCK_LATENCY_MS > 0) await sleep(env.MOCK_LATENCY_MS);

    const n = (this.calls.get(input.idempotencyKey) ?? 0) + 1;
    this.calls.set(input.idempotencyKey, n);
    const body = input.body.toLowerCase();

    // --- deterministic name triggers ---
    if (body.includes('broken')) {
      throw new ProviderError('mock: permanent failure (unreachable number)', false);
    }
    if (body.includes('doomed')) {
      throw new ProviderError(`mock: transient failure (attempt ${n})`, true);
    }
    if (body.includes('comeback') && n <= env.REMINDER_MAX_ATTEMPTS) {
      throw new ProviderError(`mock: transient failure (attempt ${n})`, true);
    }
    if (body.includes('flaky') && n < 3) {
      throw new ProviderError(`mock: transient failure (attempt ${n})`, true);
    }

    // --- outage that recovers at time T ---
    if (env.MOCK_RECOVER_AFTER_MS > 0 && process.uptime() * 1000 < env.MOCK_RECOVER_AFTER_MS) {
      throw new ProviderError('mock: provider outage', true);
    }

    // --- probabilistic failure injection ---
    if (env.MOCK_FAILURE_RATE > 0 && Math.random() < env.MOCK_FAILURE_RATE) {
      const permanent = Math.random() < env.MOCK_PERMANENT_RATE;
      throw new ProviderError(
        `mock: injected ${permanent ? 'permanent' : 'transient'} failure`,
        !permanent,
      );
    }

    return { providerMsgId: `mock_${randomUUID()}`, status: 'SENT' };
  }
}
