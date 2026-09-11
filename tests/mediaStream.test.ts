import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { AddressInfo } from 'node:net';
import WebSocket from 'ws';
import { loadConfig } from '../src/config.js';
import { logger } from '../src/logger.js';
import { startServer, type RunningServer } from '../src/server.js';

const config = loadConfig({ REALTIME_BACKEND: 'mock', TELNYX_BACKEND: 'mock', PORT: '0' });

let server: RunningServer;
let port: number;

beforeAll(async () => {
  server = await startServer(config, logger);
  port = (server.httpServer.address() as AddressInfo).port;
});

afterAll(async () => {
  await server?.close();
});

/**
 * Exercises the real Telnyx Media Streaming bridge (not the in-process demo
 * helper): connect a WebSocket, send `start` + caller `media` frames, and assert
 * the agent streams μ-law audio back and the session is cleaned up on `stop`.
 */
describe('Telnyx media-stream bridge', () => {
  it('bridges caller audio to the agent and streams audio back', async () => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}/telnyx/media`);
    const toneFrame = Buffer.alloc(160, 0x7f).toString('base64');
    let framesBack = 0;

    await new Promise<void>((resolve, reject) => {
      ws.on('error', reject);
      ws.on('open', () => {
        ws.send(JSON.stringify({ event: 'connected' }));
        ws.send(
          JSON.stringify({
            event: 'start',
            stream_id: 'test-stream-1',
            start: {
              call_control_id: 'cc-test-1',
              from: '+1',
              to: '+2',
              media_format: { encoding: 'PCMU', sample_rate: 8000, channels: 1 },
            },
          }),
        );
        let i = 0;
        const timer = setInterval(() => {
          if (i++ >= 15) {
            clearInterval(timer);
            return;
          }
          ws.send(JSON.stringify({ event: 'media', media: { payload: toneFrame } }));
        }, 15);
      });

      ws.on('message', (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.event === 'media' && msg.media?.payload) framesBack++;
      });

      // Allow greeting + one caller turn to complete.
      setTimeout(() => resolve(), 1200);
    });

    expect(framesBack).toBeGreaterThan(0);
    expect(server.registry.size).toBe(1);

    ws.send(JSON.stringify({ event: 'stop' }));
    await new Promise((r) => setTimeout(r, 150));
    expect(server.registry.size).toBe(0);
    ws.close();
  });
});
