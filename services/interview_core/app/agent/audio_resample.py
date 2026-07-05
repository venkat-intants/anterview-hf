"""Audio resampling for the avatar path.

Sarvam TTS (bulbul:v3) outputs WAV at 24000 Hz. Simli's LiveKit DataStreamAudioOutput
expects 16000 Hz PCM16 mono (verified via the Simli plugin source + research
2026-05-31). Without resampling, the avatar lip-syncs but the voice plays at the
wrong speed/pitch.

Pure linear-interpolation resampler over numpy — no scipy/librosa dependency.
Linear interp is sufficient for speech intelligibility at a 24k->16k downsample
(the bake-off can revisit quality if needed). 16-bit mono PCM in, 16-bit mono
PCM out.
"""

from __future__ import annotations

import numpy as np


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit little-endian mono PCM from ``src_rate`` to ``dst_rate``.

    Returns the resampled PCM bytes. If rates match or input is empty, returns
    the input unchanged. Never raises on normal audio input.
    """
    if not pcm or src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2")
    if samples.size == 0:
        return pcm
    n_dst = int(round(samples.size * dst_rate / src_rate))
    if n_dst <= 0:
        return b""
    # Positions in the source signal for each destination sample.
    src_idx = np.linspace(0.0, samples.size - 1, num=n_dst)
    resampled = np.interp(src_idx, np.arange(samples.size), samples.astype(np.float32))
    return np.round(resampled).astype("<i2").tobytes()
