/**
 * G.711 μ-law (PCMU) codec.
 *
 * Telnyx delivers/accepts base64-encoded RTP payloads that, for the PCMU codec,
 * are raw 8-bit μ-law samples at 8 kHz. The OpenAI GPT-Live API also speaks
 * `g711_ulaw`, so on the wire we can forward bytes unchanged. We still need a
 * real codec to synthesise audio (mock backend, comfort tones) and to build
 * playable WAV files for the demo console.
 */

const BIAS = 0x84;
const CLIP = 32635;

// Exponent lookup for the linear -> μ-law path (indexed by (sample >> 7) & 0xFF).
const EXPONENT_TABLE: readonly number[] = (() => {
  const table = new Array<number>(256).fill(7);
  for (let i = 0; i < 256; i++) {
    if (i === 0) table[i] = 0;
    else if (i === 1) table[i] = 0;
    else if (i < 4) table[i] = 1;
    else if (i < 8) table[i] = 2;
    else if (i < 16) table[i] = 3;
    else if (i < 32) table[i] = 4;
    else if (i < 64) table[i] = 5;
    else if (i < 128) table[i] = 6;
    else table[i] = 7;
  }
  return table;
})();

/** Encode a single 16-bit PCM sample to a μ-law byte. */
export function pcm16ToMulawSample(sample: number): number {
  let s = Math.max(-32768, Math.min(32767, Math.round(sample)));
  const sign = (s >> 8) & 0x80;
  if (sign !== 0) s = -s;
  if (s > CLIP) s = CLIP;
  s += BIAS;
  const exponent = EXPONENT_TABLE[(s >> 7) & 0xff] ?? 7;
  const mantissa = (s >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

/** Decode a single μ-law byte to a 16-bit PCM sample. */
export function mulawToPcm16Sample(muLawByte: number): number {
  const u = ~muLawByte & 0xff;
  const sign = u & 0x80;
  const exponent = (u >> 4) & 0x07;
  const mantissa = u & 0x0f;
  const magnitude = (((mantissa << 3) + BIAS) << exponent) - BIAS;
  return sign ? -magnitude : magnitude;
}

/** Encode a buffer of little-endian PCM16 samples to μ-law bytes. */
export function pcm16ToMulaw(pcm: Int16Array): Buffer {
  const out = Buffer.allocUnsafe(pcm.length);
  for (let i = 0; i < pcm.length; i++) {
    out[i] = pcm16ToMulawSample(pcm[i] ?? 0);
  }
  return out;
}

/** Decode μ-law bytes to an Int16Array of PCM16 samples. */
export function mulawToPcm16(mulaw: Buffer): Int16Array {
  const out = new Int16Array(mulaw.length);
  for (let i = 0; i < mulaw.length; i++) {
    out[i] = mulawToPcm16Sample(mulaw[i] ?? 0xff);
  }
  return out;
}

/** μ-law encoded silence (well-known constant, 0xFF). */
export const MULAW_SILENCE = 0xff;
