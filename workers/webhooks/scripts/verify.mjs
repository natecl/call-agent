#!/usr/bin/env node
import { generateKeyPairSync, sign } from "node:crypto";
import { parseArgs } from "node:util";
import { createHandler } from "../src/handler.ts";
import { MemorySeenStore } from "../src/seen.ts";

function publicRawB64(publicKey) {
  const der = publicKey.export({ type: "spki", format: "der" });
  return Buffer.from(der.subarray(der.length - 32)).toString("base64");
}

function signBody(privateKey, timestamp, raw) {
  const prefix = Buffer.from(`${timestamp}|`);
  return sign(null, Buffer.concat([prefix, raw]), privateKey).toString(
    "base64",
  );
}

function telnyxBody(id) {
  return Buffer.from(
    JSON.stringify({
      data: {
        id,
        event_type: "call.answered",
        payload: { call_control_id: "v3:verify" },
      },
    }),
  );
}

async function readJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function report(checks) {
  const lines = checks.map((check) => {
    const mark = check.passed ? "PASS" : "FAIL";
    return `[${mark}] ${check.name.padEnd(18)} ${check.detail}`;
  });
  const passed = checks.filter((check) => check.passed).length;
  lines.push(`${passed}/5 passed`);
  return { lines, passed: passed === checks.length && checks.length === 5 };
}

async function inProcessChecks() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const env = { TELNYX_PUBLIC_KEY: publicRawB64(publicKey) };
  const handler = createHandler(env, new MemorySeenStore());
  const origin = "https://call-agent-webhooks.verify";
  const fetchH = (path, init) =>
    handler.fetch(new Request(`${origin}${path}`, init), env);

  const healthRes = await fetchH("/health", { method: "GET" });
  const healthBody = await readJson(healthRes);
  const health = {
    name: "health",
    passed: healthRes.status === 200 && healthBody.ok === true,
    detail: `status=${healthRes.status} body=${JSON.stringify(healthBody)}`,
  };

  const unsignedRes = await fetchH("/webhooks/telnyx", {
    method: "POST",
    body: telnyxBody("webhook-unsigned"),
  });
  const raw = telnyxBody("webhook-signed-1");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signedRes = await fetchH("/webhooks/telnyx", {
    method: "POST",
    headers: {
      "telnyx-timestamp": timestamp,
      "telnyx-signature-ed25519": signBody(privateKey, timestamp, raw),
    },
    body: raw,
  });
  const signedBody = await readJson(signedRes);
  const signature = {
    name: "signature",
    passed:
      unsignedRes.status === 401 &&
      signedRes.status === 200 &&
      signedBody.ok === true &&
      signedBody.duplicate === false,
    detail:
      `unsigned=${unsignedRes.status} signed=${signedRes.status} ` +
      `duplicate=${signedBody.duplicate}`,
  };

  const again = await fetchH("/webhooks/telnyx", {
    method: "POST",
    headers: {
      "telnyx-timestamp": timestamp,
      "telnyx-signature-ed25519": signBody(privateKey, timestamp, raw),
    },
    body: raw,
  });
  const againBody = await readJson(again);
  const idempotency = {
    name: "idempotency",
    passed: again.status === 200 && againBody.duplicate === true,
    detail: `status=${again.status} duplicate=${againBody.duplicate}`,
  };

  const linqRes = await fetchH("/webhooks/linq", { method: "POST", body: "{}" });
  const linqBody = await readJson(linqRes);
  const aliasRes = await fetchH("/linq/webhook", { method: "POST", body: "{}" });
  const aliasBody = await readJson(aliasRes);
  const linq_hook = {
    name: "linq_hook",
    passed:
      linqRes.status === 200 &&
      linqBody.implemented === false &&
      aliasRes.status === 200 &&
      aliasBody.implemented === false,
    detail:
      `linq=${linqRes.status} implemented=${linqBody.implemented} ` +
      `alias=${aliasRes.status}`,
  };

  const mediaRes = await fetchH("/media-stream", { method: "GET" });
  const mediaBody = await readJson(mediaRes);
  const media_gap = {
    name: "media_gap",
    passed:
      mediaRes.status === 501 &&
      mediaBody.reason === "media_not_terminated_here",
    detail: `status=${mediaRes.status} reason=${mediaBody.reason}`,
  };

  return [health, signature, idempotency, linq_hook, media_gap];
}

async function remoteChecks(base) {
  const origin = base.replace(/\/$/, "");
  try {
    return await remoteChecksAgainst(origin);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return ["health", "signature", "idempotency", "linq_hook", "media_gap"].map(
      (name) => ({ name, passed: false, detail }),
    );
  }
}

async function remoteChecksAgainst(origin) {
  const healthRes = await fetch(`${origin}/health`);
  const healthBody = await readJson(healthRes);
  const health = {
    name: "health",
    passed: healthRes.status === 200 && healthBody.ok === true,
    detail: `status=${healthRes.status} url=${origin}/health`,
  };

  const unsignedRes = await fetch(`${origin}/webhooks/telnyx`, {
    method: "POST",
    body: telnyxBody("webhook-unsigned"),
  });
  const signature = {
    name: "signature",
    passed: unsignedRes.status === 401,
    detail:
      unsignedRes.status === 200
        ? "unsigned POST returned 200; TELNYX_PUBLIC_KEY is unwired"
        : `unsigned=${unsignedRes.status}`,
  };

  const again = await fetch(`${origin}/webhooks/telnyx`, {
    method: "POST",
    body: telnyxBody("webhook-unsigned"),
  });
  const idempotency = {
    name: "idempotency",
    passed: again.status === 401,
    detail:
      again.status === 401
        ? "remote cannot mint a Telnyx signature; unsigned stays 401"
        : `unsigned_retry=${again.status}; expected 401`,
  };

  const linqRes = await fetch(`${origin}/webhooks/linq`, {
    method: "POST",
    body: "{}",
  });
  const linqBody = await readJson(linqRes);
  const linq_hook = {
    name: "linq_hook",
    passed: linqRes.status === 200 && linqBody.implemented === false,
    detail: `status=${linqRes.status} implemented=${linqBody.implemented}`,
  };

  const mediaRes = await fetch(`${origin}/media-stream`);
  const mediaBody = await readJson(mediaRes);
  const media_gap = {
    name: "media_gap",
    passed:
      mediaRes.status === 501 &&
      mediaBody.reason === "media_not_terminated_here",
    detail: `status=${mediaRes.status} reason=${mediaBody.reason}`,
  };

  return [health, signature, idempotency, linq_hook, media_gap];
}

const { values } = parseArgs({
  options: {
    url: { type: "string" },
  },
  strict: false,
});

const checks = values.url
  ? await remoteChecks(values.url)
  : await inProcessChecks();
const { lines, passed } = report(checks);
for (const line of lines) {
  console.log(line);
}
process.exit(passed ? 0 : 1);
