import { describe, expect, it } from 'vitest';
import {
  MULAW_SILENCE,
  mulawToPcm16,
  mulawToPcm16Sample,
  pcm16ToMulaw,
  pcm16ToMulawSample,
} from '../src/audio/g711.js';

describe('g711 μ-law codec', () => {
  it('encodes digital silence to the well-known 0xFF byte', () => {
    expect(pcm16ToMulawSample(0)).toBe(MULAW_SILENCE);
  });

  it('preserves sign through a round trip', () => {
    expect(mulawToPcm16Sample(pcm16ToMulawSample(5000))).toBeGreaterThan(0);
    expect(mulawToPcm16Sample(pcm16ToMulawSample(-5000))).toBeLessThan(0);
  });

  it('round-trips within μ-law quantisation error', () => {
    for (let sample = -32000; sample <= 32000; sample += 250) {
      const decoded = mulawToPcm16Sample(pcm16ToMulawSample(sample));
      const tolerance = Math.max(256, Math.abs(sample) * 0.08);
      expect(Math.abs(decoded - sample)).toBeLessThanOrEqual(tolerance);
    }
  });

  it('encodes/decodes buffers of matching length', () => {
    const pcm = Int16Array.from([0, 1000, -1000, 16000, -16000]);
    const mulaw = pcm16ToMulaw(pcm);
    expect(mulaw).toHaveLength(pcm.length);
    const back = mulawToPcm16(mulaw);
    expect(back).toHaveLength(pcm.length);
  });

  it('produces μ-law bytes within a single byte', () => {
    for (let sample = -32768; sample < 32768; sample += 137) {
      const b = pcm16ToMulawSample(sample);
      expect(b).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThanOrEqual(255);
    }
  });
});
