"""Whisper transcription. Local inference, no data leaves the box.

Uses faster-whisper for speed. Swap the model size to trade accuracy
against latency. For transcript-only inputs this step is skipped.

PriMock57 records each consultation as two separate tracks, one per speaker.
Transcribing them independently and concatenating would destroy turn order and
hand the structurer a transcript in which the doctor asks every question before
the patient answers any of them. So merge_tracks interleaves the two tracks by
timestamp and labels the speakers, reconstructing the dialogue as it was spoken.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """One speaker's utterance, with the time it started."""

    start: float
    speaker: str
    text: str


def transcribe(audio_path: str, model_size: str = "base") -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path)
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_turns(audio_path: str, speaker: str,
                     model_size: str = "base") -> list[Turn]:
    """Transcribe one speaker's track, keeping segment start times."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path)
    return [Turn(start=seg.start, speaker=speaker, text=seg.text.strip())
            for seg in segments if seg.text.strip()]


def merge_tracks(*tracks: list[Turn]) -> str:
    """Interleave per-speaker turns by start time into a labelled dialogue.

    Pure and testable without audio, which is the point: the ordering logic is
    what can silently ruin a transcript, so it is worth pinning on its own.
    """
    turns = sorted((t for track in tracks for t in track),
                   key=lambda t: t.start)
    return "\n".join(f"{t.speaker}: {t.text}" for t in turns)


def transcribe_consultation(doctor_wav: str, patient_wav: str,
                            model_size: str = "base") -> str:
    """A PriMock57 consultation's two tracks into one speaker-labelled dialogue."""
    return merge_tracks(
        transcribe_turns(doctor_wav, "Doctor", model_size=model_size),
        transcribe_turns(patient_wav, "Patient", model_size=model_size),
    )
