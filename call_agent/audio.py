from __future__ import annotations

from array import array


def resample_pcm16(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if to_rate == 2 * from_rate:
        return _upsample_2x(samples).tobytes()
    if from_rate == 2 * to_rate:
        return _downsample_2x(samples).tobytes()
    raise ValueError(f"unsupported resample {from_rate} -> {to_rate}")


def _upsample_2x(samples: array) -> array:
    upsampled = array("h")
    for index, sample in enumerate(samples):
        following = samples[min(index + 1, len(samples) - 1)]
        upsampled.extend((sample, (sample + following) >> 1))
    return upsampled


def _downsample_2x(samples: array) -> array:
    downsampled = array(
        "h",
        (
            (first + second) >> 1
            for first, second in zip(samples[::2], samples[1::2])
        ),
    )
    if len(samples) % 2:
        downsampled.append(samples[-1])
    return downsampled
