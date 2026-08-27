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
    //   return new TwilioProvider();  // Milestone 8 — sandbox adapter for the demo
    default:
      throw new Error(`Unknown PROVIDER "${env.PROVIDER}" (expected "mock" or "twilio")`);
  }
}

// Process-wide singleton: the API, the worker, and /metrics must all observe the
// SAME provider instance — its failure counters and per-attempt log are the
// experiment data (Milestone 7).
let instance: MessageProvider | null = null;
export function getProvider(): MessageProvider {
  if (!instance) instance = createProvider();
  return instance;
}

export type { MessageProvider } from './provider';
