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
      if (
        (request.method === "POST" || request.method === "GET") &&
        (url.pathname === "/webhooks/linq" || url.pathname === "/linq/webhook")
      ) {
        return json(200, {
          ok: true,
          provider: "linq",
          implemented: false,
        });
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
