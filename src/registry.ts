import type { CallSession } from './session.js';

/** Tracks active call sessions so they can be inspected and cleaned up. */
export class SessionRegistry {
  private readonly sessions = new Map<string, CallSession>();

  add(session: CallSession): void {
    this.sessions.set(session.metadata.streamId, session);
  }

  remove(streamId: string): void {
    this.sessions.delete(streamId);
  }

  get(streamId: string): CallSession | undefined {
    return this.sessions.get(streamId);
  }

  get size(): number {
    return this.sessions.size;
  }

  list(): CallSession[] {
    return [...this.sessions.values()];
  }
}
