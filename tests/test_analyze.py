from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline import analyze as analyze_mod
from app.pipeline.analyze import (
    _analysis_windows,
    _normalize_quarter_note_grid,
    analyze,
    compute_stem_presence,
)
from app.pipeline.beat_tracker import BeatGrid


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = 44100) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def test_analysis_windows_cover_full_track_without_unbounded_chunk():
    windows = _analysis_windows(485.0)

    assert windows == [(0.0, 180.0), (180.0, 180.0), (360.0, 125.0)]
    assert sum(length for _, length in windows) == 485.0


def test_analysis_windows_sample_long_track_when_neural_beats_cover_timeline():
    windows = _analysis_windows(485.0, max_total=90.0)

    assert windows == [(0.0, 30.0), (227.5, 30.0), (455.0, 30.0)]
    assert sum(length for _, length in windows) == 90.0


def test_normalize_quarter_note_grid_corrects_half_tempo_beats():
    beats = _normalize_quarter_note_grid([0.0, 1.0, 2.0], tempo_hint=120.0)

    assert beats == [0.0, 0.5, 1.0, 1.5, 2.0]


def test_normalize_quarter_note_grid_fills_isolated_missing_beat():
    beats = _normalize_quarter_note_grid(
        [0.0, 0.5, 1.0, 2.0, 2.5],
        tempo_hint=120.0,
    )

    assert beats == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]


def test_normalize_quarter_note_grid_preserves_moderate_tempo_change():
    beats = _normalize_quarter_note_grid(
        [0.0, 0.5, 1.0, 1.75, 2.5],
        tempo_hint=100.0,
    )

    assert beats == [0.0, 0.5, 1.0, 1.75, 2.5]


def test_compute_stem_presence_uses_full_track_rms(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    loud = np.concatenate((np.zeros(44100), np.full(44100, 0.8, dtype=np.float32)))
    quiet = np.full(88200, 0.2, dtype=np.float32)
    _write_wav(stems_dir / "vocals.wav", loud)
    _write_wav(stems_dir / "bass.wav", quiet)

    presence = compute_stem_presence(stems_dir, ["vocals", "bass"])

    assert presence["vocals"] == 100
    assert 34 <= presence["bass"] <= 36


def test_analyze_collects_beats_across_all_chunks(monkeypatch, tmp_path: Path):
    starts = []

    def fake_load(*args, **kwargs):
        starts.append(kwargs["start"])
        return np.ones(2205, dtype=np.float32), 22050

    monkeypatch.setattr(analyze_mod, "_load_audio_ffmpeg", fake_load)
    monkeypatch.setattr(analyze_mod, "_measure_loudness", lambda *args: (-14.0, -1.0))

    import librosa

    monkeypatch.setattr(librosa.effects, "hpss", lambda y: (y, y))
    monkeypatch.setattr(
        librosa.beat,
        "beat_track",
        lambda **kwargs: (120.0, np.asarray([10, 20, 30], dtype=np.int64)),
    )
    monkeypatch.setattr(
        librosa,
        "frames_to_time",
        lambda frames, sr: np.asarray(frames, dtype=np.float64) * 0.5,
    )
    chroma = np.zeros((12, 2), dtype=np.float32)
    chroma[[0, 4, 7], :] = 1.0
    monkeypatch.setattr(librosa.feature, "chroma_cqt", lambda **kwargs: chroma)

    job = Job(id="abcdefabcdef", duration_sec=400.0)
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    analyze(job, source)

    assert starts == [0.0, 180.0, 360.0]
    assert job.beat_times is not None
    assert max(job.beat_times) > 360.0


def test_analyze_prefers_neural_beat_grid_when_available(monkeypatch, tmp_path: Path):
    neural = BeatGrid(
        beats=[0.1, 0.6, 1.1, 1.6],
        downbeats=[0.1],
        engine="beat_this",
        model="small0",
    )
    monkeypatch.setattr(analyze_mod, "detect_beat_grid", lambda *args: neural)
    monkeypatch.setattr(
        analyze_mod,
        "_load_audio_ffmpeg",
        lambda *args, **kwargs: (np.ones(2205, dtype=np.float32), 22050),
    )
    monkeypatch.setattr(analyze_mod, "_measure_loudness", lambda *args: (-14.0, -1.0))

    import librosa

    monkeypatch.setattr(
        librosa.effects,
        "hpss",
        lambda y: (_ for _ in ()).throw(AssertionError("HPSS should be skipped")),
    )
    monkeypatch.setattr(
        librosa.beat,
        "beat_track",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("librosa beats should be skipped")),
    )
    chroma = np.zeros((12, 2), dtype=np.float32)
    chroma[[0, 4, 7], :] = 1.0
    monkeypatch.setattr(librosa.feature, "chroma_cqt", lambda **kwargs: chroma)

    job = Job(id="abcdefabcdea", duration_sec=2.0, quality_preset="high")
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    analyze(job, source)

    assert job.beat_times == neural.beats
    assert job.downbeat_times == neural.downbeats
    assert job.beat_tracker == "beat_this:small0"
