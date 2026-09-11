import { createServer as createHttpServer, type Server } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express, { type Express } from 'express';
import { WebSocketServer } from 'ws';
import type { AppConfig } from './config.js';
import { logger, type Logger } from './logger.js';
import { SessionRegistry } from './registry.js';
import { createTelnyxClient, type TelnyxClient } from './telnyx/client.js';
import { createWebhookRouter } from './telnyx/webhook.js';
import { handleMediaStream } from './telnyx/mediaStream.js';
import { runSimulatedCall } from './demo/simulate.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.resolve(__dirname, '../public');

export interface AppDeps {
  telnyx: TelnyxClient;
  registry: SessionRegistry;
  log: Logger;
}

export function createApp(config: AppConfig, deps: AppDeps): Express {
  const { telnyx, registry, log } = deps;
  const app = express();
  app.use(express.json({ limit: '2mb' }));

  app.get('/healthz', (_req, res) => {
    res.json({
      status: 'ok',
      realtimeBackend: config.realtimeBackend,
      telnyxBackend: telnyx.mode,
      activeCalls: registry.size,
    });
  });

  app.get('/api/status', (_req, res) => {
    res.json({
      service: 'call-agent',
      realtimeBackend: config.realtimeBackend,
      telnyxBackend: telnyx.mode,
      model: config.OPENAI_MODEL,
      voice: config.OPENAI_VOICE,
      activeCalls: registry.size,
      streamTrack: config.TELNYX_STREAM_TRACK,
    });
  });

  app.use('/telnyx', createWebhookRouter(config, telnyx, log));

  app.post('/demo/simulate', async (req, res) => {
    try {
      const callerLines: string[] | undefined = Array.isArray(req.body?.callerLines)
        ? req.body.callerLines
        : undefined;
      const result = await runSimulatedCall(config, log, { callerLines });
      res.json(result);
    } catch (err) {
      log.error({ err }, 'Simulated call failed');
      res.status(500).json({ error: (err as Error).message });
    }
  });

  app.use(express.static(PUBLIC_DIR));

  return app;
}

export interface RunningServer {
  httpServer: Server;
  registry: SessionRegistry;
  close: () => Promise<void>;
}

export function startServer(config: AppConfig, log: Logger = logger): Promise<RunningServer> {
  const registry = new SessionRegistry();
  const telnyx = createTelnyxClient(config, log);
  const app = createApp(config, { telnyx, registry, log });
  const httpServer = createHttpServer(app);

  const wss = new WebSocketServer({ noServer: true });
  httpServer.on('upgrade', (request, socket, head) => {
    const { url } = request;
    if (url && url.startsWith('/telnyx/media')) {
      wss.handleUpgrade(request, socket, head, (ws) => {
        handleMediaStream(ws, config, registry, log);
      });
    } else {
      socket.destroy();
    }
  });

  return new Promise((resolve) => {
    httpServer.listen(config.PORT, config.HOST, () => {
      log.info(
        {
          port: config.PORT,
          host: config.HOST,
          realtimeBackend: config.realtimeBackend,
          telnyxBackend: telnyx.mode,
        },
        'call-agent listening',
      );
      resolve({
        httpServer,
        registry,
        close: () =>
          new Promise<void>((res) => {
            wss.close();
            httpServer.close(() => res());
          }),
      });
    });
  });
}
