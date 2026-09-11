import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';

const TELNYX_API_BASE = 'https://api.telnyx.com/v2';

export interface StartStreamingOptions {
  callControlId: string;
  streamUrl: string;
  streamTrack?: string;
}

/**
 * Minimal Telnyx Call Control client covering the commands this agent issues:
 * answering an inbound call and starting bidirectional RTP media streaming.
 */
export interface TelnyxClient {
  readonly mode: 'live' | 'mock';
  answerCall(callControlId: string): Promise<void>;
  startStreaming(options: StartStreamingOptions): Promise<void>;
  hangup(callControlId: string): Promise<void>;
}

class LiveTelnyxClient implements TelnyxClient {
  readonly mode = 'live' as const;

  constructor(
    private readonly config: AppConfig,
    private readonly log: Logger,
  ) {}

  private async post(path: string, body: Record<string, unknown>): Promise<void> {
    const res = await fetch(`${TELNYX_API_BASE}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.config.TELNYX_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Telnyx API ${path} failed: ${res.status} ${text}`);
    }
  }

  async answerCall(callControlId: string): Promise<void> {
    this.log.info({ callControlId }, 'Answering call');
    await this.post(`/calls/${callControlId}/actions/answer`, {});
  }

  async startStreaming(options: StartStreamingOptions): Promise<void> {
    this.log.info({ callControlId: options.callControlId, streamUrl: options.streamUrl }, 'Starting media stream');
    await this.post(`/calls/${options.callControlId}/actions/streaming_start`, {
      stream_url: options.streamUrl,
      stream_track: options.streamTrack ?? this.config.TELNYX_STREAM_TRACK,
      stream_bidirectional_mode: 'rtp',
      stream_bidirectional_codec: 'PCMU',
    });
  }

  async hangup(callControlId: string): Promise<void> {
    this.log.info({ callControlId }, 'Hanging up call');
    await this.post(`/calls/${callControlId}/actions/hangup`, {});
  }
}

class MockTelnyxClient implements TelnyxClient {
  readonly mode = 'mock' as const;

  constructor(private readonly log: Logger) {}

  answerCall(callControlId: string): Promise<void> {
    this.log.info({ callControlId }, '[mock] answer call');
    return Promise.resolve();
  }

  startStreaming(options: StartStreamingOptions): Promise<void> {
    this.log.info({ ...options }, '[mock] start streaming');
    return Promise.resolve();
  }

  hangup(callControlId: string): Promise<void> {
    this.log.info({ callControlId }, '[mock] hangup');
    return Promise.resolve();
  }
}

export function createTelnyxClient(config: AppConfig, log: Logger): TelnyxClient {
  return config.telnyxBackend === 'live'
    ? new LiveTelnyxClient(config, log)
    : new MockTelnyxClient(log);
}
