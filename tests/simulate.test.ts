import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { logger } from '../src/logger.js';
import { runSimulatedCall } from '../src/demo/simulate.js';

const config = loadConfig({ REALTIME_BACKEND: 'mock', TELNYX_BACKEND: 'mock' });

describe('end-to-end simulated call (mock backend)', () => {
  it('greets, handles caller turns, and streams agent audio back', async () => {
    const result = await runSimulatedCall(config, logger, {
      callerLines: ['Hello there', 'What are your hours?'],
    });

    const agentTurns = result.transcript.filter((t) => t.role === 'agent');
    const callerTurns = result.transcript.filter((t) => t.role === 'caller');

    // Greeting + one reply per caller line.
    expect(agentTurns.length).toBe(3);
    expect(callerTurns.length).toBe(2);

    // Audio flowed in both directions.
    expect(result.metrics.framesFromCaller).toBeGreaterThan(0);
    expect(result.metrics.framesToCaller).toBeGreaterThan(0);
    expect(result.metrics.bytesToCaller).toBeGreaterThan(0);

    // A playable WAV of the agent audio was produced.
    const wav = Buffer.from(result.agentAudioWavBase64, 'base64');
    expect(wav.subarray(0, 4).toString('ascii')).toBe('RIFF');

    expect(result.metadata.backend).toBe('mock');
  });

  it('uses the configured greeting as the first agent turn', async () => {
    const custom = loadConfig({
      REALTIME_BACKEND: 'mock',
      TELNYX_BACKEND: 'mock',
      GREETING: 'Welcome to Acme support.',
    });
    const result = await runSimulatedCall(custom, logger, { callerLines: [] });
    expect(result.transcript[0]?.role).toBe('agent');
    expect(result.transcript[0]?.text).toContain('Acme');
  });
});
