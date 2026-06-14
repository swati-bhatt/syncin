// The swappable messaging boundary. The whole engine talks to THIS interface,
// never to Twilio/WhatsApp directly — so the mock provider can drive every
// benchmark and a real adapter can be dropped in for a demo without touching
// the worker, retry, or idempotency logic.

export interface SendMessageInput {
  to: string; // E.164
  body: string;
  // Lets a provider dedupe on its side; the mock echoes it. This is how we get
  // at-most-once delivery even though our queue is at-least-once.
  idempotencyKey: string;
}

export interface SendMessageResult {
  providerMsgId: string;
  status: 'QUEUED' | 'SENT';
}

export interface MessageProvider {
  readonly name: string;
  sendMessage(input: SendMessageInput): Promise<SendMessageResult>;
}

// Workers will branch on `retryable` (Milestone 4): transient → backoff + retry,
// permanent → straight to the dead-letter queue.
export class ProviderError extends Error {
  readonly retryable: boolean;
  constructor(message: string, retryable: boolean) {
    super(message);
    this.retryable = retryable;
  }
}
