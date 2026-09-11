import { mulawToPcm16 } from './g711.js';
import { SAMPLE_RATE } from './tone.js';

/**
 * Wrap μ-law audio in a PCM16 WAV container so browsers can play it back in the
 * demo console. Returns a base64 data payload.
 */
export function mulawToWavBase64(mulaw: Buffer, sampleRate = SAMPLE_RATE): string {
  const pcm = mulawToPcm16(mulaw);
  const dataSize = pcm.length * 2;
  const buffer = Buffer.alloc(44 + dataSize);

  buffer.write('RIFF', 0, 'ascii');
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write('WAVE', 8, 'ascii');
  buffer.write('fmt ', 12, 'ascii');
  buffer.writeUInt32LE(16, 16); // fmt chunk size
  buffer.writeUInt16LE(1, 20); // PCM
  buffer.writeUInt16LE(1, 22); // mono
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28); // byte rate
  buffer.writeUInt16LE(2, 32); // block align
  buffer.writeUInt16LE(16, 34); // bits per sample
  buffer.write('data', 36, 'ascii');
  buffer.writeUInt32LE(dataSize, 40);

  for (let i = 0; i < pcm.length; i++) {
    buffer.writeInt16LE(pcm[i] ?? 0, 44 + i * 2);
  }

  return buffer.toString('base64');
}
