import { describe, expect, it } from 'vitest';
import { FRAME_SAMPLES, generateSpokenTone, generateTone, toFrames } from '../src/audio/tone.js';
import { mulawToWavBase64 } from '../src/audio/wav.js';

describe('tone generation', () => {
  it('produces one μ-law byte per sample', () => {
    const tone = generateTone({ frequency: 440, durationMs: 100 });
    expect(tone).toHaveLength(800); // 100 ms @ 8 kHz
  });

  it('is not pure silence', () => {
    const tone = generateTone({ frequency: 440, durationMs: 100 });
    const nonSilent = [...tone].some((b) => b !== 0xff);
    expect(nonSilent).toBe(true);
  });

  it('splits into 20 ms frames', () => {
    const tone = generateSpokenTone(1);
    const frames = toFrames(tone);
    expect(frames.length).toBeGreaterThan(0);
    for (const frame of frames.slice(0, -1)) {
      expect(frame).toHaveLength(FRAME_SAMPLES);
    }
  });
});

describe('wav container', () => {
  it('wraps μ-law audio in a valid RIFF/WAVE header', () => {
    const tone = generateTone({ frequency: 440, durationMs: 50 });
    const wav = Buffer.from(mulawToWavBase64(tone), 'base64');
    expect(wav.subarray(0, 4).toString('ascii')).toBe('RIFF');
    expect(wav.subarray(8, 12).toString('ascii')).toBe('WAVE');
    // data section = 2 bytes per μ-law sample.
    expect(wav.length).toBe(44 + tone.length * 2);
  });
});
