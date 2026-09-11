import { EventEmitter } from 'node:events';
import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import { generateSpokenTone, toFrames } from '../audio/tone.js';
import type { RealtimeEvents, RealtimeSession } from './types.js';

const CANNED_REPLIES = [
  "Sure, I can help with that. Could you tell me a little more?",
  "Got it. Let me take care of that for you.",
  "Thanks for waiting. Is there anything else I can do?",
  "No problem at all. I've noted that down.",
];

/**
 * In-process realtime backend used when no OpenAI key is configured. It emulates
 * GPT-Live turn taking: it greets on request and, after a caller "utterance"
 * (a burst of appended audio followed by a short pause), replies with a canned
 * transcript plus synthesised μ-law audio. This makes the full bidirectional
 * call flow runnable and testable without any credentials.
 */
export class MockRealtimeSession implements RealtimeSession {
  private readonly emitter = new EventEmitter();
  private turnTimer: NodeJS.Timeout | null = null;
  private replyIndex = 0;
  private closed = false;
  private framesSinceTurn = 0;

  /** Pause (ms) after the last caller frame that triggers a response. */
  readonly turnDebounceMs: number;

  constructor(
    private readonly config: AppConfig,
    private readonly log: Logger,
    options: { turnDebounceMs?: number } = {},
  ) {
    this.turnDebounceMs = options.turnDebounceMs ?? 150;
  }

  start(): Promise<void> {
    this.log.info('Mock realtime backend started (no OpenAI credentials in use)');
    return Promise.resolve();
  }

  requestGreeting(): void {
    // Slight delay to mimic model latency and keep ordering realistic.
    setTimeout(() => this.respond(this.config.GREETING), 20);
  }

  appendAudio(_base64Mulaw: string): void {
    if (this.closed) return;
    this.framesSinceTurn++;
    if (this.turnTimer) clearTimeout(this.turnTimer);
    this.turnTimer = setTimeout(() => this.onCallerTurnEnd(), this.turnDebounceMs);
  }

  private onCallerTurnEnd(): void {
    if (this.closed || this.framesSinceTurn === 0) return;
    this.emitter.emit('speechStarted');
    this.framesSinceTurn = 0;
    const reply = CANNED_REPLIES[this.replyIndex % CANNED_REPLIES.length] ?? 'Okay.';
    this.replyIndex++;
    this.respond(reply);
  }

  private respond(text: string): void {
    if (this.closed) return;
    // Stream the transcript in a couple of chunks.
    const words = text.split(' ');
    const mid = Math.ceil(words.length / 2);
    this.emitter.emit('transcript', words.slice(0, mid).join(' ') + ' ');
    this.emitter.emit('transcript', words.slice(mid).join(' '));

    const audio = generateSpokenTone(this.replyIndex);
    for (const frame of toFrames(audio)) {
      this.emitter.emit('audio', frame.toString('base64'));
    }
    this.emitter.emit('responseDone');
  }

  close(): void {
    this.closed = true;
    if (this.turnTimer) clearTimeout(this.turnTimer);
    this.turnTimer = null;
    this.emitter.emit('close');
  }

  on<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void {
    this.emitter.on(event, listener as (...args: unknown[]) => void);
  }

  off<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void {
    this.emitter.off(event, listener as (...args: unknown[]) => void);
  }
}
