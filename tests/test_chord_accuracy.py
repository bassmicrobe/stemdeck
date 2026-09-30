from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.pipeline import chords as chords_mod
from app.pipeline.chords import detect_chord_segments


def _triad_audio(root: int, minor: bool, frames: int, sr: int, cents: float) -> np.ndarray:
    third = 3 if minor else 4
    semitones = (root, root + third, root + 7)
    t = np.arange(frames, dtype=np.float32) / sr
    signal = np.zeros(frames, dtype=np.float32)
    for idx, semitone in enumerate(semitones):
        frequency = 261.6256 * (2 ** ((semitone + cents / 100.0) / 12))
        signal += np.sin(2 * np.pi * frequency * t).astype(np.float32) * (0.25 - idx * 0.03)
    bass = 65.4064 * (2 ** ((root + cents / 100.0) / 12))
    signal += np.sin(2 * np.pi * bass * t).astype(np.float32) * 0.22
    fade = min(frames // 8, int(sr * 0.02))
    signal[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
    signal[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return signal


@pytest.mark.parametrize("cents", [0.0, 30.0])
def test_audio_chord_estimator_tracks_pop_progression(
    monkeypatch,
    tmp_path: Path,
    cents: float,
):
    sr = 22050
    chord_frames = sr
    progression = [(0, False), (7, False), (9, True), (5, False)]
    audio = np.concatenate(
        [_triad_audio(root, minor, chord_frames, sr, cents) for root, minor in progression]
    )
    rng = np.random.default_rng(7)
    audio += rng.normal(0, 0.002, len(audio)).astype(np.float32)

    def fake_load(_path, sr=22050, duration=180.0, *, start=0.0, job=None):
        del job
        left = int(start * sr)
        right = min(len(audio), left + int(duration * sr))
        return audio[left:right], sr

    monkeypatch.setattr(chords_mod, "_load_audio_ffmpeg", fake_load)
    source = tmp_path / "progression.wav"
    source.write_bytes(b"fixture")

    segments = detect_chord_segments(
        source,
        [idx * 0.5 for idx in range(9)],
        duration_sec=4.0,
    )

    assert [segment.label for segment in segments] == ["C", "G", "Am", "F"]
    assert [(segment.start_beat, segment.end_beat) for segment in segments] == [
        (0, 2),
        (2, 4),
        (4, 6),
        (6, 8),
    ]
