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


# ---------- P1-4: PriMock57 records each speaker on its own track ----------
# The merge is pure, so it is tested without audio and runs in CI. Getting this
# wrong would hand the structurer a transcript where the doctor asks every
# question before the patient answers any of them, and the SOAP note would be
# scored against that. Worth pinning on its own.

def test_merge_interleaves_two_speaker_tracks_by_time():
    from services.intake.transcribe import Turn, merge_tracks

    doctor = [Turn(0.0, "Doctor", "What brings you in?"),
              Turn(6.0, "Doctor", "How long?")]
    patient = [Turn(3.0, "Patient", "A cough."),
               Turn(9.0, "Patient", "Three days.")]

    assert merge_tracks(doctor, patient) == (
        "Doctor: What brings you in?\n"
        "Patient: A cough.\n"
        "Doctor: How long?\n"
        "Patient: Three days."
    )


def test_merge_does_not_simply_concatenate_the_tracks():
    # The regression that matters: concatenation would put both doctor turns
    # first and destroy the question-answer structure of the consultation.
    from services.intake.transcribe import Turn, merge_tracks

    merged = merge_tracks([Turn(0.0, "Doctor", "Q1"), Turn(6.0, "Doctor", "Q2")],
                          [Turn(3.0, "Patient", "A1")])
    assert merged.splitlines()[1].startswith("Patient:")


def test_merge_of_an_empty_track_is_safe():
    from services.intake.transcribe import Turn, merge_tracks

    assert merge_tracks([], [Turn(1.0, "Patient", "Hello")]) == "Patient: Hello"
    assert merge_tracks([], []) == ""
