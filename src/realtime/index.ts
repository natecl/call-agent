import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import { MockRealtimeSession } from './mock.js';
import { OpenAIRealtimeSession } from './openai.js';
import type { RealtimeSession } from './types.js';

export function createRealtimeSession(config: AppConfig, log: Logger): RealtimeSession {
  if (config.realtimeBackend === 'openai') {
    return new OpenAIRealtimeSession(config, log);
  }
  return new MockRealtimeSession(config, log);
}

export type { RealtimeSession } from './types.js';
