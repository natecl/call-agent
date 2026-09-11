import { describe, expect, it, vi, beforeEach } from 'vitest';
import request from 'supertest';
import { loadConfig } from '../src/config.js';
import { logger } from '../src/logger.js';
import { SessionRegistry } from '../src/registry.js';
import type { TelnyxClient } from '../src/telnyx/client.js';
import { createApp } from '../src/server.js';

function makeTelnyxSpy(): TelnyxClient & {
  answerCall: ReturnType<typeof vi.fn>;
  startStreaming: ReturnType<typeof vi.fn>;
  hangup: ReturnType<typeof vi.fn>;
} {
  return {
    mode: 'mock',
    answerCall: vi.fn().mockResolvedValue(undefined),
    startStreaming: vi.fn().mockResolvedValue(undefined),
    hangup: vi.fn().mockResolvedValue(undefined),
  };
}

const config = loadConfig({ REALTIME_BACKEND: 'mock', TELNYX_BACKEND: 'mock' });

describe('Telnyx webhook', () => {
  let telnyx: ReturnType<typeof makeTelnyxSpy>;
  let app: ReturnType<typeof createApp>;

  beforeEach(() => {
    telnyx = makeTelnyxSpy();
    app = createApp(config, { telnyx, registry: new SessionRegistry(), log: logger });
  });

  it('answers an inbound call on call.initiated', async () => {
    await request(app)
      .post('/telnyx/webhook')
      .send({
        data: {
          event_type: 'call.initiated',
          payload: { call_control_id: 'cc-1', direction: 'incoming', from: '+1', to: '+2' },
        },
      })
      .expect(200);

    await vi.waitFor(() => expect(telnyx.answerCall).toHaveBeenCalledWith('cc-1'));
    expect(telnyx.startStreaming).not.toHaveBeenCalled();
  });

  it('starts media streaming on call.answered', async () => {
    await request(app)
      .post('/telnyx/webhook')
      .send({ data: { event_type: 'call.answered', payload: { call_control_id: 'cc-2' } } })
      .expect(200);

    await vi.waitFor(() =>
      expect(telnyx.startStreaming).toHaveBeenCalledWith(
        expect.objectContaining({ callControlId: 'cc-2' }),
      ),
    );
  });

  it('ignores events without a call_control_id', async () => {
    await request(app)
      .post('/telnyx/webhook')
      .send({ data: { event_type: 'call.hangup', payload: {} } })
      .expect(200);
    expect(telnyx.answerCall).not.toHaveBeenCalled();
  });

  it('reports health with backend info', async () => {
    const res = await request(app).get('/healthz').expect(200);
    expect(res.body).toMatchObject({ status: 'ok', telnyxBackend: 'mock' });
  });
});
