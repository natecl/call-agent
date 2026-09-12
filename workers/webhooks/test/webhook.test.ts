import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { test } from "node:test";
import { createHandler } from "../src/handler.ts";
import { MemorySeenStore } from "../src/seen.ts";
import { parseTelnyxId, verifyTelnyx } from "../src/telnyx.ts";

function publicRawB64(publicKey: ReturnType<typeof generateKeyPairSync>["publicKey"]) {
  const der = publicKey.export({ type: "spki", format: "der" }) as Buffer;
  return Buffer.from(der.subarray(der.length - 32)).toString("base64");
}

function signBody(
  privateKey: ReturnType<typeof generateKeyPairSync>["privateKey"],
  timestamp: string,
  raw: Uint8Array,
) {
  return sign(
    null,
    Buffer.concat([Buffer.from(`${timestamp}|`), raw]),
    privateKey,
  ).toString("base64");
}

function body(id: string) {
  return new TextEncoder().encode(
    JSON.stringify({
      data: {
        id,
        event_type: "call.answered",
        payload: { call_control_id: "v3:verify" },
      },
    }),
  );
}

test("parseTelnyxId reads data.id", () => {
  assert.equal(parseTelnyxId(body("abc")), "abc");
  assert.equal(parseTelnyxId(new TextEncoder().encode("{}")), null);
});

test("verifyTelnyx accepts a matching Ed25519 signature", async () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = body("evt-1");
  const timestamp = "1700000000";
  const signature = signBody(privateKey, timestamp, raw);
  const result = await verifyTelnyx(
    raw,
    timestamp,
    signature,
    publicRawB64(publicKey),
    1700000000,
  );
  assert.deepEqual(result, { ok: true });
});

test("verifyTelnyx rejects a stale timestamp", async () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = body("evt-2");
  const timestamp = "1000";
  const signature = signBody(privateKey, timestamp, raw);
  const result = await verifyTelnyx(
    raw,
    timestamp,
    signature,
    publicRawB64(publicKey),
    1700000000,
  );
  assert.deepEqual(result, { ok: false, reason: "stale_timestamp" });
});

test("createHandler verifies, claims, and fails media loud", async () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const env = { TELNYX_PUBLIC_KEY: publicRawB64(publicKey) };
  const handler = createHandler(env, new MemorySeenStore());
  const origin = "https://example.test";
  const raw = body("evt-3");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const first = await handler.fetch(
    new Request(`${origin}/webhooks/telnyx`, {
      method: "POST",
      headers: {
        "telnyx-timestamp": timestamp,
        "telnyx-signature-ed25519": signBody(privateKey, timestamp, raw),
      },
      body: raw,
    }),
    env,
  );
  assert.equal(first.status, 200);
  assert.equal((await first.json()).duplicate, false);

  const second = await handler.fetch(
    new Request(`${origin}/webhooks/telnyx`, {
      method: "POST",
      headers: {
        "telnyx-timestamp": timestamp,
        "telnyx-signature-ed25519": signBody(privateKey, timestamp, raw),
      },
      body: raw,
    }),
    env,
  );
  assert.equal(second.status, 200);
  assert.equal((await second.json()).duplicate, true);

  const unsigned = await handler.fetch(
    new Request(`${origin}/webhooks/telnyx`, {
      method: "POST",
      body: raw,
    }),
    env,
  );
  assert.equal(unsigned.status, 401);

  const linq = await handler.fetch(
    new Request(`${origin}/webhooks/linq`, { method: "POST", body: "{}" }),
    env,
  );
  assert.deepEqual(await linq.json(), {
    ok: true,
    provider: "linq",
    implemented: false,
  });

  const media = await handler.fetch(
    new Request(`${origin}/media-stream`),
    env,
  );
  assert.equal(media.status, 501);
  assert.equal((await media.json()).reason, "media_not_terminated_here");
});
