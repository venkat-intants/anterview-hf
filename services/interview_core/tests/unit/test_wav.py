"""Unit tests for the minimal WAV header builder (S3-006).

Verifies the 44-byte RIFF/WAVE header for PCM 16-bit mono is well-formed so
Sarvam's batch STT endpoint accepts the wrapped audio.
"""

from __future__ import annotations

import struct

from app.speech._wav import _pcm_to_wav


def test_pcm_to_wav_zero_bytes_produces_header_only() -> None:
    """100 bytes of zeros → 144-byte WAV (44 header + 100 data)."""
    pcm = b"\x00" * 100
    wav = _pcm_to_wav(pcm)

    assert len(wav) == 144, f"expected 144 bytes, got {len(wav)}"
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"


def test_pcm_to_wav_default_sample_rate_is_16khz() -> None:
    """Sample rate field at offset 24 (4 bytes LE) must be 16000."""
    wav = _pcm_to_wav(b"\x01\x02\x03\x04")
    (sample_rate,) = struct.unpack("<I", wav[24:28])
    assert sample_rate == 16000


def test_pcm_to_wav_riff_size_includes_pcm_length() -> None:
    """RIFF chunk size at offset 4 = (44 - 8) + len(pcm) = 36 + data_size."""
    pcm = b"\xaa" * 256
    wav = _pcm_to_wav(pcm)
    (riff_size,) = struct.unpack("<I", wav[4:8])
    assert riff_size == 36 + 256


def test_pcm_to_wav_data_chunk_size_matches_pcm_length() -> None:
    """data sub-chunk size at offset 40 (4 bytes LE) must equal len(pcm)."""
    pcm = b"\x7f" * 512
    wav = _pcm_to_wav(pcm)
    (data_size,) = struct.unpack("<I", wav[40:44])
    assert data_size == 512


def test_pcm_to_wav_format_is_pcm_mono_16bit() -> None:
    """fmt subchunk encodes: audio_format=1 (PCM), num_channels=1, bits_per_sample=16."""
    wav = _pcm_to_wav(b"")
    (audio_format,) = struct.unpack("<H", wav[20:22])
    (num_channels,) = struct.unpack("<H", wav[22:24])
    (bits_per_sample,) = struct.unpack("<H", wav[34:36])
    assert audio_format == 1
    assert num_channels == 1
    assert bits_per_sample == 16


def test_pcm_to_wav_byte_rate_and_block_align_are_consistent() -> None:
    """byte_rate = sample_rate * channels * bytes_per_sample; block_align = channels * bps."""
    wav = _pcm_to_wav(b"", sample_rate=16000)
    (byte_rate,) = struct.unpack("<I", wav[28:32])
    (block_align,) = struct.unpack("<H", wav[32:34])
    # 16000 Hz * 1 channel * 2 bytes/sample = 32000
    assert byte_rate == 32000
    # 1 channel * 2 bytes/sample = 2
    assert block_align == 2


def test_pcm_to_wav_passes_through_pcm_bytes_unmodified() -> None:
    """The PCM payload must appear verbatim immediately after the 44-byte header."""
    pcm = bytes(range(256))
    wav = _pcm_to_wav(pcm)
    assert wav[44:] == pcm
