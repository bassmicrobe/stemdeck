from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from app.pipeline.benchmark import (
    benchmark_audio,
    benchmark_job_dir,
    chord_metrics,
    stem_sum_metrics,
)


def _write_wav(path: Path, samples: np.ndarray, sr: int = 8000) -> None:
    sf.write(path, samples.astype(np.float32), sr, subtype="FLOAT")


def test_stem_sum_metrics_reports_near_zero_residual(tmp_path: Path):
    sr = 8000
    t = np.linspace(0, 1, sr, endpoint=False)
    bass = np.sin(2 * np.pi * 110 * t) * 0.2
    drums = np.sin(2 * np.pi * 440 * t) * 0.1
    source = bass + drums
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(tmp_path / "source.wav", source, sr)
    _write_wav(stems_dir / "bass.wav", bass, sr)
    _write_wav(stems_dir / "drums.wav", drums, sr)

    metrics = stem_sum_metrics(
        tmp_path / "source.wav",
        stems_dir,
        stem_names=["bass", "drums"],
        sr=sr,
        duration=1.0,
    )

    assert metrics["available"] is True
    assert metrics["used_stems"] == ["bass", "drums"]
    assert metrics["residual_percent"] < 0.01
    assert metrics["correlation"] > 0.999


def test_stem_sum_metrics_reports_residual_when_stem_missing(tmp_path: Path):
    sr = 8000
    t = np.linspace(0, 1, sr, endpoint=False)
    bass = np.sin(2 * np.pi * 110 * t) * 0.2
    drums = np.sin(2 * np.pi * 440 * t) * 0.1
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(tmp_path / "source.wav", bass + drums, sr)
    _write_wav(stems_dir / "bass.wav", bass, sr)

    metrics = stem_sum_metrics(
        tmp_path / "source.wav",
        stems_dir,
        stem_names=["bass", "drums"],
        sr=sr,
        duration=1.0,
    )

    assert metrics["missing_stems"] == ["drums"]
    assert metrics["residual_percent"] > 40


def test_chord_metrics_summarizes_metadata(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "chords.mid").write_bytes(b"MThd")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "bpm": 128,
                "beat_times": [0.0, 0.5, 1.0],
                "chord_progression": [
                    {"label": "C", "confidence": 0.8},
                    {"label": "G", "confidence": 0.6},
                ],
            }
        ),
        encoding="utf-8",
    )

    metrics = chord_metrics(metadata, stems_dir)

    assert metrics["metadata_available"] is True
    assert metrics["midi_available"] is True
    assert metrics["segment_count"] == 2
    assert metrics["unique_labels"] == ["C", "G"]
    assert metrics["average_confidence"] == 0.7


def test_benchmark_job_dir_uses_retained_source(tmp_path: Path):
    sr = 8000
    source = np.zeros(sr, dtype=np.float32)
    job_dir = tmp_path / "abcdefabcdef"
    stems_dir = job_dir / "stems"
    stems_dir.mkdir(parents=True)
    _write_wav(job_dir / "source.wav", source, sr)
    _write_wav(stems_dir / "vocals.wav", source, sr)
    (job_dir / "metadata.json").write_text("{}", encoding="utf-8")

    report = benchmark_job_dir(job_dir, sr=sr, duration=1.0)

    assert report["schema"] == "layerlab-benchmark-v1"
    assert report["stem_sum"]["available"] is True


def test_benchmark_audio_without_source_reports_chords_only(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(stems_dir / "vocals.wav", np.zeros(16, dtype=np.float32), 8000)

    report = benchmark_audio(source=None, stems_dir=stems_dir, sr=8000, duration=1.0)

    assert report["stem_sum"]["available"] is False
    assert report["stems"]["present"] == ["vocals"]
