import { pcm16ToMulaw } from './g711.js';

export const SAMPLE_RATE = 8000;
export const FRAME_SAMPLES = 160; // 20 ms @ 8 kHz
export const FRAME_MS = 20;

/**
 * Generate a μ-law encoded sine tone of the given duration. Used by the mock
 * realtime backend to produce audible "speech" so the bidirectional media path
 * can be demonstrated without a live model.
 */
export function generateTone(options: {
  frequency: number;
  durationMs: number;
  amplitude?: number;
  sampleRate?: number;
}): Buffer {
  const { frequency, durationMs } = options;
  const amplitude = options.amplitude ?? 0.35;
  const sampleRate = options.sampleRate ?? SAMPLE_RATE;
  const totalSamples = Math.round((durationMs / 1000) * sampleRate);
  const pcm = new Int16Array(totalSamples);
  const fadeSamples = Math.min(80, Math.floor(totalSamples / 8));

  for (let i = 0; i < totalSamples; i++) {
    let gain = amplitude;
    // Short fade in/out to avoid clicks.
    if (i < fadeSamples) gain *= i / fadeSamples;
    else if (i > totalSamples - fadeSamples) gain *= (totalSamples - i) / fadeSamples;
    pcm[i] = Math.round(gain * 32767 * Math.sin((2 * Math.PI * frequency * i) / sampleRate));
  }

  return pcm16ToMulaw(pcm);
}

/**
 * Build a short musical "utterance" from a sequence of tones, returned as a
 * single μ-law buffer. This gives the mock backend a distinctive, deterministic
 * audio reply.
 */
export function generateSpokenTone(seed = 0): Buffer {
  const notes = [
    { frequency: 440 + (seed % 3) * 40, durationMs: 180 },
    { frequency: 550 + (seed % 5) * 30, durationMs: 160 },
    { frequency: 660, durationMs: 220 },
  ];
  return Buffer.concat(notes.map((n) => generateTone(n)));
}

/** Split a μ-law buffer into fixed-size (20 ms) frames for streaming. */
export function toFrames(mulaw: Buffer, frameSamples = FRAME_SAMPLES): Buffer[] {
  const frames: Buffer[] = [];
  for (let offset = 0; offset < mulaw.length; offset += frameSamples) {
    frames.push(mulaw.subarray(offset, Math.min(offset + frameSamples, mulaw.length)));
  }
  return frames;
}
