"""Whisper transcription. Local inference, no data leaves the box.

Uses faster-whisper for speed. Swap the model size to trade accuracy
against latency. For transcript-only inputs this step is skipped.
"""
from __future__ import annotations


def transcribe(audio_path: str, model_size: str = "base") -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path)
    return " ".join(seg.text.strip() for seg in segments).strip()
