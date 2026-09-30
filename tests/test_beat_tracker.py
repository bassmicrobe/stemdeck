from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from app.core.models import Job
from app.pipeline import beat_tracker


def test_auto_tracker_uses_accurate_neural_model_for_all_profiles():
    assert beat_tracker.should_use_beat_this("standard") is True
    assert beat_tracker.should_use_beat_this("high") is True
    assert beat_tracker.should_use_beat_this("max") is True
    assert beat_tracker.beat_this_model_for_quality("high") == "final0"
    assert beat_tracker.beat_this_model_for_quality("standard") == "final0"
    assert beat_tracker.beat_this_model_for_quality("max") == "final0"


def test_detect_beat_grid_returns_validated_neural_results(monkeypatch, tmp_path):
    class FakeTracker:
        def __call__(self, _path):
            return np.asarray([1.1, 0.1, 0.6, 0.6]), np.asarray([0.1, 2.5])

    monkeypatch.setattr(beat_tracker, "beat_this_available", lambda: True)
    monkeypatch.setattr(
        beat_tracker,
        "_tracker",
        lambda model, device: (FakeTracker(), threading.Lock()),
    )
    job = Job(id="abcdefabcdef", duration_sec=2.0, quality_preset="high")
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    result = beat_tracker.detect_beat_grid(job, source)

    assert result is not None
    assert result.beats == [0.1, 0.6, 1.1]
    assert result.downbeats == [0.1]
    assert result.model == "final0"


def test_detect_beat_grid_prepares_m4a_for_neural_tracker(monkeypatch, tmp_path):
    seen: list[str] = []

    class FakeTracker:
        def __call__(self, path):
            seen.append(path)
            return np.asarray([0.0, 0.5, 1.0]), np.asarray([0.0])

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(job, cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"wav")
        return Result()

    monkeypatch.setattr(beat_tracker, "beat_this_available", lambda: True)
    monkeypatch.setattr(beat_tracker, "_tracker", lambda *args: (FakeTracker(), threading.Lock()))
    monkeypatch.setattr(beat_tracker, "run_tracked_process", fake_run, raising=False)
    source = tmp_path / "source.m4a"
    source.write_bytes(b"m4a")
    job = Job(id="abcdefabcdea", duration_sec=2.0, quality_preset="standard")

    result = beat_tracker.detect_beat_grid(job, source)

    assert result is not None
    assert seen == [str(tmp_path / "source.beats.wav")]
    assert result.analysis_source == tmp_path / "source.beats.wav"
