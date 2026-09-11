import { EventEmitter } from 'node:events';
import WebSocket from 'ws';
import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import type { RealtimeEvents, RealtimeSession } from './types.js';

const REALTIME_URL = 'wss://api.openai.com/v1/realtime';

/**
 * OpenAI GPT-Live (Realtime API, GA) session over WebSocket.
 *
 * Uses the nested GA audio schema with `g711_ulaw` in and out so audio can be
 * relayed to/from Telnyx without transcoding. Server-side VAD drives turn
 * taking, so we simply append caller audio and stream back `output_audio.delta`.
 */
export class OpenAIRealtimeSession implements RealtimeSession {
  private ws: WebSocket | null = null;
  private readonly emitter = new EventEmitter();
  private ready = false;

  constructor(
    private readonly config: AppConfig,
    private readonly log: Logger,
  ) {}

  start(): Promise<void> {
    if (!this.config.OPENAI_API_KEY) {
      return Promise.reject(new Error('OPENAI_API_KEY is required for the OpenAI backend'));
    }

    const url = `${REALTIME_URL}?model=${encodeURIComponent(this.config.OPENAI_MODEL)}`;
    const ws = new WebSocket(url, {
      headers: { Authorization: `Bearer ${this.config.OPENAI_API_KEY}` },
    });
    this.ws = ws;

    return new Promise<void>((resolve, reject) => {
      const onError = (err: Error) => {
        this.log.error({ err }, 'OpenAI realtime connection error');
        reject(err);
      };

      ws.once('error', onError);
      ws.on('open', () => {
        ws.off('error', onError);
        this.configureSession();
        this.ready = true;
        resolve();
      });
      ws.on('message', (data) => this.handleMessage(data));
      ws.on('error', (err) => this.emitter.emit('error', err));
      ws.on('close', () => {
        this.ready = false;
        this.emitter.emit('close');
      });
    });
  }

  private configureSession(): void {
    this.send({
      type: 'session.update',
      session: {
        type: 'realtime',
        instructions: this.config.SYSTEM_PROMPT,
        output_modalities: ['audio'],
        audio: {
          input: {
            format: { type: 'g711_ulaw' },
            turn_detection: { type: 'server_vad' },
          },
          output: {
            format: { type: 'g711_ulaw' },
            voice: this.config.OPENAI_VOICE,
          },
        },
      },
    });
  }

  requestGreeting(): void {
    this.send({
      type: 'response.create',
      response: {
        instructions: `Greet the caller: "${this.config.GREETING}"`,
      },
    });
  }

  appendAudio(base64Mulaw: string): void {
    if (!this.ready) return;
    this.send({ type: 'input_audio_buffer.append', audio: base64Mulaw });
  }

  private handleMessage(data: WebSocket.RawData): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let msg: any;
    try {
      msg = JSON.parse(data.toString());
    } catch {
      return;
    }

    switch (msg.type) {
      // Support both GA (`response.output_audio.delta`) and legacy names.
      case 'response.output_audio.delta':
      case 'response.audio.delta':
        if (typeof msg.delta === 'string') this.emitter.emit('audio', msg.delta);
        break;
      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta':
        if (typeof msg.delta === 'string') this.emitter.emit('transcript', msg.delta);
        break;
      case 'input_audio_buffer.speech_started':
        this.emitter.emit('speechStarted');
        break;
      case 'response.done':
        this.emitter.emit('responseDone');
        break;
      case 'error':
        this.log.error({ error: msg.error }, 'OpenAI realtime error event');
        this.emitter.emit('error', new Error(msg.error?.message ?? 'OpenAI realtime error'));
        break;
      default:
        break;
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private send(payload: any): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  close(): void {
    this.ready = false;
    this.ws?.close();
    this.ws = null;
  }

  on<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void {
    this.emitter.on(event, listener as (...args: unknown[]) => void);
  }

  off<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void {
    this.emitter.off(event, listener as (...args: unknown[]) => void);
  }
}
