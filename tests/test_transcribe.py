"""P1-1: Whisper transcription yields a non-empty transcript, model size
is configurable. Uses a real PriMock57 clip, trimmed short for test speed.
Skipped when the dataset is not downloaded (see scripts/download_data.md).
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_AUDIO = (REPO_ROOT / "data" / "primock57" / "audio"
                 / "day1_consultation01_doctor.wav")
_CLIP_SECONDS = 12


def _make_short_clip(tmp_path: Path) -> str:
    clip_path = tmp_path / "clip.wav"
    with wave.open(str(_SOURCE_AUDIO), "rb") as src:
        params = src.getparams()
        n_frames = min(params.nframes, params.framerate * _CLIP_SECONDS)
        frames = src.readframes(n_frames)
    with wave.open(str(clip_path), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(frames)
    return str(clip_path)


pytestmark = pytest.mark.skipif(
    not _SOURCE_AUDIO.is_file(), reason="primock57 dataset not downloaded")


def test_transcription_is_non_empty(tmp_path):
    from services.intake.transcribe import transcribe

    clip = _make_short_clip(tmp_path)
    text = transcribe(clip, model_size="tiny")
    assert isinstance(text, str)
    assert text.strip() != ""


def test_model_size_is_configurable(tmp_path):
    import inspect

    from services.intake.transcribe import transcribe

    params = inspect.signature(transcribe).parameters
    assert "model_size" in params
    assert params["model_size"].default == "base"

    clip = _make_short_clip(tmp_path)
    tiny_text = transcribe(clip, model_size="tiny")
    assert tiny_text.strip() != ""
