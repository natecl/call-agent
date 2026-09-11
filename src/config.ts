import { z } from 'zod';
import { logger } from './logger.js';

/**
 * Runtime configuration derived from environment variables.
 *
 * The agent is designed to run end-to-end without live credentials: when the
 * OpenAI or Telnyx keys are absent we fall back to in-process mock backends so
 * the full call flow can still be exercised (see {@link resolveBackends}).
 */
const envSchema = z.object({
  NODE_ENV: z.string().default('development'),
  PORT: z.coerce.number().int().min(0).max(65535).default(3000),
  HOST: z.string().default('0.0.0.0'),

  /** Publicly reachable wss host Telnyx should stream media to (no scheme). */
  PUBLIC_HOST: z.string().optional(),

  TELNYX_API_KEY: z.string().optional(),
  TELNYX_PUBLIC_KEY: z.string().optional(),
  TELNYX_STREAM_TRACK: z
    .enum(['inbound_track', 'outbound_track', 'both_tracks'])
    .default('inbound_track'),

  OPENAI_API_KEY: z.string().optional(),
  OPENAI_MODEL: z.string().default('gpt-realtime'),
  OPENAI_VOICE: z.string().default('marin'),

  SYSTEM_PROMPT: z
    .string()
    .default(
      'You are a friendly, concise consumer phone assistant. Keep replies short and natural for a voice call.',
    ),
  GREETING: z.string().default('Hi! Thanks for calling. How can I help you today?'),

  /** Force a backend regardless of credential presence. Useful for demos/tests. */
  REALTIME_BACKEND: z.enum(['auto', 'openai', 'mock']).default('auto'),
  TELNYX_BACKEND: z.enum(['auto', 'live', 'mock']).default('auto'),
});

export type RawEnv = z.infer<typeof envSchema>;

export interface AppConfig extends RawEnv {
  realtimeBackend: 'openai' | 'mock';
  telnyxBackend: 'live' | 'mock';
}

function resolveBackends(env: RawEnv): Pick<AppConfig, 'realtimeBackend' | 'telnyxBackend'> {
  const realtimeBackend =
    env.REALTIME_BACKEND === 'auto'
      ? env.OPENAI_API_KEY
        ? 'openai'
        : 'mock'
      : env.REALTIME_BACKEND;

  const telnyxBackend =
    env.TELNYX_BACKEND === 'auto' ? (env.TELNYX_API_KEY ? 'live' : 'mock') : env.TELNYX_BACKEND;

  return { realtimeBackend, telnyxBackend };
}

let cached: AppConfig | null = null;

export function loadConfig(overrides: Partial<NodeJS.ProcessEnv> = {}): AppConfig {
  const parsed = envSchema.safeParse({ ...process.env, ...overrides });
  if (!parsed.success) {
    logger.error({ issues: parsed.error.issues }, 'Invalid environment configuration');
    throw new Error('Invalid environment configuration');
  }

  const config: AppConfig = { ...parsed.data, ...resolveBackends(parsed.data) };
  return config;
}

export function getConfig(): AppConfig {
  if (!cached) {
    cached = loadConfig();
  }
  return cached;
}

/** Reset the cached config. Only used by tests. */
export function resetConfig(): void {
  cached = null;
}
