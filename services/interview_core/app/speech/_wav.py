"""Minimal WAV header builder (S3-006).

Wraps raw PCM 16-bit little-endian mono audio in a 44-byte RIFF/WAVE header
so the bytes can be POSTed to the Sarvam batch STT endpoint (which auto-
detects format from the WAV header). We deliberately avoid scipy / soundfile
to keep the dependency surface small — ``struct`` is enough for a fixed
PCM mono header.

Spec reference: http://soundfile.sapp.org/doc/WaveFormat/ — the canonical
"Canonical WAVE file format" diagram.

NOT a general-purpose WAV writer:
  - PCM 16-bit only (audio format code = 1).
  - Mono only (1 channel).
  - Sample rate is parameterised but defaults to 16 kHz (Sarvam recommended).
  - No fact / list / cue chunks; only fmt + data.

PII note: this module receives raw audio bytes — it MUST NOT log them.
"""

from __future__ import annotations

import struct

# Fixed format constants ----------------------------------------------------
_PCM_FORMAT_CODE: int = 1  # WAVE_FORMAT_PCM
_NUM_CHANNELS: int = 1  # mono
_BITS_PER_SAMPLE: int = 16
_BYTES_PER_SAMPLE: int = _BITS_PER_SAMPLE // 8  # 2
_HEADER_SIZE_BYTES: int = 44


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Prepend a 44-byte RIFF/WAVE header to ``pcm_bytes``.

    Args:
        pcm_bytes: Raw PCM 16-bit LE mono samples. Empty input is allowed
            (produces a header-only 44-byte WAV — the STT call will then
            reject it as empty, which surfaces as ``STTError``).
        sample_rate: Sample rate in Hz. Default 16000 (Sarvam recommended).

    Returns:
        ``len(pcm_bytes) + 44`` bytes — a complete PCM mono WAV file.
    """
    byte_rate: int = sample_rate * _NUM_CHANNELS * _BYTES_PER_SAMPLE
    block_align: int = _NUM_CHANNELS * _BYTES_PER_SAMPLE
    data_size: int = len(pcm_bytes)
    # RIFF chunk size = file size - 8 (RIFF + size fields are not counted).
    riff_size: int = _HEADER_SIZE_BYTES - 8 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt sub-chunk size for PCM
        _PCM_FORMAT_CODE,
        _NUM_CHANNELS,
        sample_rate,
        byte_rate,
        block_align,
        _BITS_PER_SAMPLE,
        b"data",
        data_size,
    )
    return header + pcm_bytes
