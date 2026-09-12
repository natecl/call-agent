export const REPLAY_WINDOW_S = 300;

export type VerifyResult =
  | { ok: true }
  | { ok: false; reason: "bad_signature" | "stale_timestamp" };

function bytes(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

export async function verifyTelnyx(
  raw: Uint8Array,
  timestamp: string,
  signatureB64: string,
  publicKeyB64: string,
  nowSec: number,
): Promise<VerifyResult> {
  const stamped = Number(timestamp);
  if (!Number.isFinite(stamped) || Math.abs(nowSec - stamped) > REPLAY_WINDOW_S) {
    return { ok: false, reason: "stale_timestamp" };
  }
  let signature: Uint8Array;
  let publicKeyRaw: Uint8Array;
  try {
    signature = Uint8Array.from(atob(signatureB64), (c) => c.charCodeAt(0));
    publicKeyRaw = Uint8Array.from(atob(publicKeyB64), (c) => c.charCodeAt(0));
  } catch {
    return { ok: false, reason: "bad_signature" };
  }
  if (signature.byteLength !== 64 || publicKeyRaw.byteLength !== 32) {
    return { ok: false, reason: "bad_signature" };
  }
  const prefix = bytes(`${timestamp}|`);
  const signed = new Uint8Array(prefix.byteLength + raw.byteLength);
  signed.set(prefix);
  signed.set(raw, prefix.byteLength);
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      publicKeyRaw,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const ok = await crypto.subtle.verify("Ed25519", key, signature, signed);
    return ok ? { ok: true } : { ok: false, reason: "bad_signature" };
  } catch {
    return { ok: false, reason: "bad_signature" };
  }
}

export function parseTelnyxId(raw: Uint8Array): string | null {
  try {
    const parsed = JSON.parse(new TextDecoder().decode(raw)) as {
      data?: { id?: unknown };
    };
    if (typeof parsed.data?.id === "string" && parsed.data.id.length > 0) {
      return parsed.data.id;
    }
    return null;
  } catch {
    return null;
  }
}
