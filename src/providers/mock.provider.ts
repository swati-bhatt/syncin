import { randomUUID } from 'node:crypto';
import type { MessageProvider, SendMessageInput, SendMessageResult } from './provider';

// The default provider. Always succeeds for now.
//
// Milestone 6 turns this into the experiment rig: configurable failure rate,
// transient-vs-permanent errors, injected latency, duplicate deliveries, and a
// "recovers at time T" mode — which is exactly what Paper 1 measures.
export class MockProvider implements MessageProvider {
  readonly name = 'mock';

  async sendMessage(_input: SendMessageInput): Promise<SendMessageResult> {
    return { providerMsgId: `mock_${randomUUID()}`, status: 'SENT' };
  }
}
