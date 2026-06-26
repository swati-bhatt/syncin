import { randomUUID } from 'node:crypto';
import {
  ProviderError,
  type MessageProvider,
  type SendMessageInput,
  type SendMessageResult,
} from './provider';
import { env } from '../config/env';

// The default provider. Succeeds by default, with simple DETERMINISTIC failure
// injection for exercising Milestone 4's retry / dead-letter / replay logic. The
// trigger is a marker in the recipient's name (which shows up in the message body):
//
//   "flaky"    -> transient failure on attempts 1-2, succeeds on the 3rd (recovers via retry)
//   "doomed"   -> transient failure that NEVER recovers (-> DLQ after retries)
//   "broken"   -> permanent, non-retryable failure (-> DLQ immediately)
//   "comeback" -> fails the whole first run (-> DLQ), then succeeds when REPLAYED
//
// Everything else just succeeds. Milestone 6 generalizes this into the full
// experiment rig (failure rate, latency, duplicate delivery, "recovers at time T").
export class MockProvider implements MessageProvider {
  readonly name = 'mock';
  private calls = new Map<string, number>(); // attempts seen per idempotencyKey

  async sendMessage(input: SendMessageInput): Promise<SendMessageResult> {
    const n = (this.calls.get(input.idempotencyKey) ?? 0) + 1;
    this.calls.set(input.idempotencyKey, n);
    const body = input.body.toLowerCase();

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

    return { providerMsgId: `mock_${randomUUID()}`, status: 'SENT' };
  }
}
