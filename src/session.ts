import type { Logger } from './logger.js';
import type { RealtimeSession } from './realtime/index.js';

/** Destination for audio the agent wants to play back to the caller. */
export interface MediaSink {
  sendAudio(base64Mulaw: string): void;
}

export interface CallMetadata {
  streamId: string;
  callControlId?: string;
  from?: string;
  to?: string;
}

export interface TranscriptEntry {
  role: 'agent' | 'caller';
  text: string;
  at: number;
}

export interface CallMetrics {
  framesFromCaller: number;
  framesToCaller: number;
  bytesToCaller: number;
}

/**
 * Orchestrates a single phone call: it wires caller audio (from the Telnyx
 * media stream) into the realtime model and streams the model's audio back to
 * the caller, while collecting a transcript and metrics for observability.
 */
export class CallSession {
  readonly metadata: CallMetadata;
  readonly transcript: TranscriptEntry[] = [];
  readonly metrics: CallMetrics = { framesFromCaller: 0, framesToCaller: 0, bytesToCaller: 0 };
  /** Raw μ-law audio the agent produced (kept for demo playback). */
  readonly agentAudio: Buffer[] = [];

  private pendingAgentText = '';
  private closed = false;

  constructor(
    metadata: CallMetadata,
    private readonly realtime: RealtimeSession,
    private readonly sink: MediaSink,
    private readonly log: Logger,
  ) {
    this.metadata = metadata;
    this.bindRealtime();
  }

  private bindRealtime(): void {
    this.realtime.on('audio', (base64Mulaw) => {
      if (this.closed) return;
      this.metrics.framesToCaller++;
      const buf = Buffer.from(base64Mulaw, 'base64');
      this.metrics.bytesToCaller += buf.length;
      this.agentAudio.push(buf);
      this.sink.sendAudio(base64Mulaw);
    });

    this.realtime.on('transcript', (text) => {
      this.pendingAgentText += text;
    });

    this.realtime.on('responseDone', () => {
      const text = this.pendingAgentText.trim();
      if (text) {
        this.transcript.push({ role: 'agent', text, at: Date.now() });
        this.log.info({ streamId: this.metadata.streamId, text }, 'Agent response');
      }
      this.pendingAgentText = '';
    });

    this.realtime.on('error', (err) => {
      this.log.error({ err, streamId: this.metadata.streamId }, 'Realtime error');
    });
  }

  async start(): Promise<void> {
    await this.realtime.start();
    this.realtime.requestGreeting();
  }

  /** Handle an inbound μ-law audio frame from the caller. */
  handleCallerAudio(base64Mulaw: string): void {
    if (this.closed) return;
    this.metrics.framesFromCaller++;
    this.realtime.appendAudio(base64Mulaw);
  }

  /** Record a (simulated) caller utterance in the transcript. */
  noteCallerUtterance(text: string): void {
    this.transcript.push({ role: 'caller', text, at: Date.now() });
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.realtime.close();
    this.log.info(
      { streamId: this.metadata.streamId, metrics: this.metrics },
      'Call session closed',
    );
  }
}
