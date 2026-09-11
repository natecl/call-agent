/**
 * Abstraction over a speech-to-speech "GPT-Live" backend. The Telnyx media
 * bridge only depends on this interface, which lets us swap between the real
 * OpenAI Realtime API and an in-process mock used for credential-free demos and
 * tests.
 */
export interface RealtimeEvents {
  /** Emitted with base64 μ-law audio the model wants played to the caller. */
  audio: (base64Mulaw: string) => void;
  /** Emitted with incremental transcript text from the model. */
  transcript: (text: string) => void;
  /** Emitted when the model finishes a response turn. */
  responseDone: () => void;
  /** Emitted when the caller's speech is (server-side) detected as started. */
  speechStarted: () => void;
  error: (err: Error) => void;
  close: () => void;
}

export interface RealtimeSession {
  /** Establish the session and configure audio format / persona. */
  start(): Promise<void>;
  /** Forward a base64 μ-law audio frame from the caller to the model. */
  appendAudio(base64Mulaw: string): void;
  /** Ask the model to greet the caller / start speaking. */
  requestGreeting(): void;
  close(): void;

  on<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void;
  off<E extends keyof RealtimeEvents>(event: E, listener: RealtimeEvents[E]): void;
}
