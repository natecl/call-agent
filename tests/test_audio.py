import struct

import pytest

from call_agent.audio import resample_pcm16


def pcm(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def test_resample_pcm16_upsamples_8k_to_16k_with_floor_midpoints() -> None:
    assert resample_pcm16(pcm(0, 100, -100, 50), 8000, 16000) == pcm(
        0, 50, 100, 0, -100, -25, 50, 50
    )


def test_resample_pcm16_downsamples_16k_to_8k_by_pair_means() -> None:
    assert resample_pcm16(
        pcm(0, 50, 100, 0, -100, -25, 50, 50), 16000, 8000
    ) == pcm(25, 50, -63, 50)
    assert resample_pcm16(pcm(10, 20, 30), 16000, 8000) == pcm(15, 30)


def test_resample_pcm16_passes_same_rate_and_empty_input_through() -> None:
    assert resample_pcm16(b"\x01\x02", 16000, 16000) == b"\x01\x02"
    assert resample_pcm16(b"", 8000, 16000) == b""


def test_resample_pcm16_rejects_unsupported_ratio_and_odd_byte_count() -> None:
    with pytest.raises(ValueError, match="unsupported resample 8000 -> 48000"):
        resample_pcm16(pcm(0), 8000, 48000)
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00", 8000, 16000)
