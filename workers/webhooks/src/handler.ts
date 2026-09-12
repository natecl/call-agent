import { isLinqPath, postLinq } from "./linq.ts";
import type { Env, Receipt, SeenStore } from "./receipt.ts";
import { parseTelnyxId, verifyTelnyx } from "./telnyx.ts";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function statusFor(receipt: Receipt): number {
  if (receipt.kind !== "rejected") {
    return 200;
  }
  if (
    receipt.reason === "missing_public_key" ||
    receipt.reason === "bad_signature" ||
    receipt.reason === "stale_timestamp"
  ) {
    return 401;
  }
  if (receipt.reason === "media_not_terminated_here") {
    return 501;
  }
  return 400;
}

export async function receiveLinq(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method === "GET") {
    return json(200, { ok: true, provider: "linq" });
  }
  if (request.method !== "POST") {
    return json(404, { ok: false, reason: "not_found" });
  }
  const raw = new Uint8Array(await request.arrayBuffer());
  const result = await postLinq(raw, request.headers, env.APP_BASE_URL);
  if (result.kind === "missing_app_base_url") {
    return json(501, { ok: false, reason: "missing_app_base_url" });
  }
  if (result.kind === "invalid_app_base_url") {
    return json(501, { ok: false, reason: "invalid_app_base_url" });
  }
  if (result.kind === "app_unreachable") {
    return json(502, { ok: false, reason: "app_unreachable" });
  }
  const text = await result.response.text();
  try {
    return json(result.response.status, JSON.parse(text) as unknown);
  } catch {
    return new Response(text, {
      status: result.response.status,
      headers: { "content-type": "application/json" },
    });
  }
}

export async function receiveTelnyx(
  raw: Uint8Array,
  timestamp: string,
  signature: string,
  env: Env,
  seen: SeenStore,
  nowSec: number,
): Promise<Receipt> {
  const publicKey = env.TELNYX_PUBLIC_KEY?.trim();
  if (!publicKey) {
    return { kind: "rejected", reason: "missing_public_key" };
  }
  const verified = await verifyTelnyx(
    raw,
    timestamp,
    signature,
    publicKey,
    nowSec,
  );
  if (!verified.ok) {
    return { kind: "rejected", reason: verified.reason };
  }
  const id = parseTelnyxId(raw);
  if (id === null) {
    return { kind: "rejected", reason: "malformed" };
  }
  const claim = await seen.claim(id);
  if (claim === "duplicate") {
    return { kind: "duplicate", id, provider: "telnyx" };
  }
  return { kind: "accepted", id, provider: "telnyx" };
}

export function createHandler(env: Env, seen: SeenStore) {
  return {
    async fetch(
      request: Request,
      requestEnv: Env = env,
      ctx?: { waitUntil(promise: Promise<unknown>): void },
    ): Promise<Response> {
      const active = requestEnv;
      const url = new URL(request.url);
      if (url.pathname === "/health" && request.method === "GET") {
        return json(200, { ok: true });
      }
      if (url.pathname === "/media-stream") {
        return json(501, { ok: false, reason: "media_not_terminated_here" });
      }
      if (isLinqPath(url.pathname)) {
        return receiveLinq(request, active);
      }
      if (request.method === "POST" && url.pathname === "/webhooks/telnyx") {
        const raw = new Uint8Array(await request.arrayBuffer());
        const receipt = await receiveTelnyx(
          raw,
          request.headers.get("telnyx-timestamp") ?? "",
          request.headers.get("telnyx-signature-ed25519") ?? "",
          active,
          seen,
          Math.floor(Date.now() / 1000),
        );
        if (receipt.kind === "accepted") {
          const upstream = active.UPSTREAM_WEBHOOK_URL?.trim();
          if (upstream && ctx) {
            const headers = new Headers(request.headers);
            ctx.waitUntil(
              fetch(upstream, {
                method: "POST",
                headers,
                body: raw,
              }).catch(() => undefined),
            );
          }
        }
        if (receipt.kind === "rejected") {
          return json(statusFor(receipt), { ok: false, reason: receipt.reason });
        }
        return json(200, {
          ok: true,
          duplicate: receipt.kind === "duplicate",
          id: receipt.id,
        });
      }
      return json(404, { ok: false, reason: "not_found" });
    },
  };
}
