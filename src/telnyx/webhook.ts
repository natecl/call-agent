import { Router, type Request, type Response } from 'express';
import type { AppConfig } from '../config.js';
import type { Logger } from '../logger.js';
import type { TelnyxClient } from './client.js';

function mediaStreamUrl(config: AppConfig): string {
  const host = config.PUBLIC_HOST ?? `localhost:${config.PORT}`;
  const scheme = config.PUBLIC_HOST ? 'wss' : 'ws';
  return `${scheme}://${host}/telnyx/media`;
}

/**
 * Express router for Telnyx Call Control webhooks. Answers inbound calls and,
 * once answered, starts bidirectional RTP media streaming to our WebSocket
 * endpoint so audio can be bridged to the realtime model.
 */
export function createWebhookRouter(config: AppConfig, telnyx: TelnyxClient, log: Logger): Router {
  const router = Router();

  router.post('/webhook', async (req: Request, res: Response) => {
    const event = req.body?.data;
    const eventType: string | undefined = event?.event_type;
    const payload = event?.payload ?? {};
    const callControlId: string | undefined = payload.call_control_id;

    log.info({ eventType, callControlId, from: payload.from, to: payload.to }, 'Telnyx webhook');

    // Acknowledge immediately; issue call commands asynchronously.
    res.status(200).json({ received: true });

    if (!eventType || !callControlId) return;

    try {
      switch (eventType) {
        case 'call.initiated':
          if (payload.direction === 'incoming') {
            await telnyx.answerCall(callControlId);
          }
          break;
        case 'call.answered':
          await telnyx.startStreaming({
            callControlId,
            streamUrl: mediaStreamUrl(config),
          });
          break;
        default:
          break;
      }
    } catch (err) {
      log.error({ err, eventType, callControlId }, 'Failed to handle Telnyx webhook');
    }
  });

  return router;
}
