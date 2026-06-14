import { env } from '../config/env';
import { MockProvider } from './mock.provider';
import type { MessageProvider } from './provider';

// Provider factory — selected by env.PROVIDER. The rest of the app depends only
// on the MessageProvider interface, so swapping mock ⇄ twilio is a one-line change.
export function createProvider(): MessageProvider {
  switch (env.PROVIDER) {
    case 'mock':
      return new MockProvider();
    // case 'twilio':
    //   return new TwilioProvider();  // Milestone 7 — sandbox adapter for the demo GIF
    default:
      throw new Error(`Unknown PROVIDER "${env.PROVIDER}" (expected "mock" or "twilio")`);
  }
}

export type { MessageProvider } from './provider';
