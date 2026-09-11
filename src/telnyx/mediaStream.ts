import type { WebSocket } from 'ws';
import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import { createRealtimeSession } from '../realtime/index.js';
import { CallSession, type MediaSink } from '../session.js';
import type { SessionRegistry } from '../registry.js';

/**
 * Handle a single Telnyx Media Streaming WebSocket connection.
 *
 * Telnyx sends `connected`, then `start` (with stream metadata and media
 * format), then a series of `media` frames (base64 μ-law), and finally `stop`.
 * We create a {@link CallSession} on `start`, forward caller frames to the
 * realtime model, and stream model audio back as `media` frames.
 */
export function handleMediaStream(
  ws: WebSocket,
  config: AppConfig,
  registry: SessionRegistry,
  log: Logger,
): void {
  let session: CallSession | null = null;

  const sink: MediaSink = {
    sendAudio(base64Mulaw: string) {
      if (ws.readyState === ws.OPEN) {
        ws.send(JSON.stringify({ event: 'media', media: { payload: base64Mulaw } }));
      }
    },
  };

  ws.on('message', (data) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let msg: any;
    try {
      msg = JSON.parse(data.toString());
    } catch {
      return;
    }

    switch (msg.event) {
      case 'connected':
        log.debug('Telnyx media stream connected');
        break;
      case 'start': {
        const start = msg.start ?? {};
        const streamId: string = msg.stream_id ?? start.stream_id ?? `stream-${Date.now()}`;
        const realtime = createRealtimeSession(config, log);
        session = new CallSession(
          {
            streamId,
            callControlId: start.call_control_id,
            from: start.from,
            to: start.to,
          },
          realtime,
          sink,
          log,
        );
        registry.add(session);
        session.start().catch((err) => log.error({ err }, 'Failed to start call session'));
        break;
      }
      case 'media':
        if (session && msg.media?.payload) {
          session.handleCallerAudio(msg.media.payload);
        }
        break;
      case 'stop':
        if (session) {
          registry.remove(session.metadata.streamId);
          session.close();
          session = null;
        }
        break;
      default:
        break;
    }
  });

  ws.on('close', () => {
    if (session) {
      registry.remove(session.metadata.streamId);
      session.close();
      session = null;
    }
  });

  ws.on('error', (err) => log.error({ err }, 'Telnyx media stream socket error'));
}
