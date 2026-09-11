import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import { generateTone, toFrames } from '../audio/tone.js';
import { mulawToWavBase64 } from '../audio/wav.js';
import { createRealtimeSession } from '../realtime/index.js';
import type { RealtimeSession } from '../realtime/index.js';
import { CallSession, type CallMetrics, type MediaSink, type TranscriptEntry } from '../session.js';

export interface SimulatedCallResult {
  metadata: { streamId: string; from: string; to: string; backend: string; telnyx: string };
  transcript: TranscriptEntry[];
  metrics: CallMetrics;
  durationMs: number;
  /** Base64 WAV (PCM16, 8 kHz) of the agent's synthesised audio. */
  agentAudioWavBase64: string;
}

const DEFAULT_CALLER_LINES = [
  "Hi, I'd like to check my order status.",
  "It's order number 12345.",
  'Great, thank you!',
];

function waitForResponse(realtime: RealtimeSession, timeoutMs = 3000): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      realtime.off('responseDone', done);
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(done, timeoutMs);
    realtime.on('responseDone', done);
  });
}

/**
 * Drive a complete inbound call end-to-end in-process: greeting, several caller
 * turns, and the agent's spoken replies. Works with the mock backend (no
 * credentials) and with the real OpenAI backend if configured.
 */
export async function runSimulatedCall(
  config: AppConfig,
  log: Logger,
  options: { callerLines?: string[]; from?: string; to?: string } = {},
): Promise<SimulatedCallResult> {
  const callerLines = options.callerLines ?? DEFAULT_CALLER_LINES;
  const from = options.from ?? '+13125550123';
  const to = options.to ?? '+13125559876';
  const streamId = `sim-${Date.now()}`;
  const start = Date.now();

  // The CallSession already captures agent audio; the sink is a no-op here.
  const sink: MediaSink = { sendAudio: () => {} };
  const realtime = createRealtimeSession(config, log);
  const session = new CallSession({ streamId, from, to }, realtime, sink, log);

  const greetingDone = waitForResponse(realtime);
  await session.start();
  await greetingDone;

  for (const line of callerLines) {
    session.noteCallerUtterance(line);
    const responseDone = waitForResponse(realtime);
    // Synthesise ~300 ms of caller audio so the backend registers a turn.
    const callerAudio = generateTone({ frequency: 300, durationMs: 300, amplitude: 0.4 });
    for (const frame of toFrames(callerAudio)) {
      session.handleCallerAudio(frame.toString('base64'));
    }
    await responseDone;
  }

  session.close();

  // Order transcript chronologically (caller utterances are noted before the reply).
  const transcript = [...session.transcript].sort((a, b) => a.at - b.at);
  const agentAudio = Buffer.concat(session.agentAudio);

  return {
    metadata: {
      streamId,
      from,
      to,
      backend: config.realtimeBackend,
      telnyx: config.telnyxBackend,
    },
    transcript,
    metrics: session.metrics,
    durationMs: Date.now() - start,
    agentAudioWavBase64: mulawToWavBase64(agentAudio),
  };
}
