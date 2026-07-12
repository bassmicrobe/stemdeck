from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from app.pipeline.benchmark import (
    benchmark_audio,
    benchmark_job_dir,
    benchmark_jobs_root,
    chord_metrics,
    compare_benchmark_reports,
    reference_chord_metrics,
    reference_stem_metrics,
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


def test_stem_sum_metrics_preserves_stereo_channels_for_peak_measurement(tmp_path: Path):
    sr = 8000
    stereo = np.column_stack(
        (
            np.full(sr, 0.9, dtype=np.float32),
            np.full(sr, -0.9, dtype=np.float32),
        )
    )
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(tmp_path / "source.wav", stereo, sr)
    _write_wav(stems_dir / "vocals.wav", stereo, sr)

    metrics = stem_sum_metrics(
        tmp_path / "source.wav",
        stems_dir,
        stem_names=["vocals"],
        sr=sr,
        duration=1.0,
    )

    assert metrics["source_rms"] > 0.89
    assert metrics["source_peak_dbfs"] < 0
    assert metrics["stem_sum_clipping_samples"] == 0


def test_reference_stem_metrics_reports_isolation_not_only_reconstruction(tmp_path: Path):
    sr = 8000
    t = np.linspace(0, 1, sr, endpoint=False)
    bass = np.sin(2 * np.pi * 110 * t) * 0.25
    vocals = np.sin(2 * np.pi * 440 * t) * 0.2
    reference_dir = tmp_path / "reference"
    estimated_dir = tmp_path / "estimated"
    reference_dir.mkdir()
    estimated_dir.mkdir()
    _write_wav(reference_dir / "bass.wav", bass, sr)
    _write_wav(reference_dir / "vocals.wav", vocals, sr)
    _write_wav(estimated_dir / "bass.wav", bass + (vocals * 0.4), sr)
    _write_wav(estimated_dir / "vocals.wav", vocals, sr)

    metrics = reference_stem_metrics(
        reference_dir,
        estimated_dir,
        stem_names=["bass", "vocals"],
        sr=sr,
        duration=1.0,
    )

    assert metrics["available"] is True
    assert metrics["per_stem"]["vocals"]["si_sdr_db"] > 80
    assert metrics["per_stem"]["bass"]["si_sdr_db"] < 15
    assert metrics["per_stem"]["bass"]["worst_crosstalk_stem"] == "vocals"
    assert metrics["summary"]["si_sdr_db_mean"] < metrics["per_stem"]["vocals"]["si_sdr_db"]


def test_chord_metrics_summarizes_metadata(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "chords.mid").write_bytes(b"MThd")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "bpm": 128,
                "duration_sec": 2.0,
                "beat_times": [0.0, 0.5, 1.0],
                "chord_progression": [
                    {"label": "C", "confidence": 0.8, "start": 0.0, "end": 1.0, "start_beat": 0, "end_beat": 2},
                    {"label": "Gmaj7", "confidence": 0.6, "start": 1.0, "end": 1.5, "start_beat": 2, "end_beat": 3},
                ],
            }
        ),
        encoding="utf-8",
    )

    metrics = chord_metrics(metadata, stems_dir)

    assert metrics["metadata_available"] is True
    assert metrics["midi_available"] is True
    assert metrics["segment_count"] == 2
    assert metrics["unique_labels"] == ["C", "Gmaj7"]
    assert metrics["average_confidence"] == 0.7
    assert metrics["average_beats_per_segment"] == 1.5
    assert metrics["short_segment_count"] == 1
    assert metrics["unstable_short_segment_count"] == 1


def test_reference_chord_metrics_reports_mir_eval_wcsr(tmp_path: Path):
    reference = tmp_path / "reference.lab"
    reference.write_text("0.0\t1.0\tC:maj\n1.0\t2.0\tG:maj\n", encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "chord_progression": [
                    {"label": "C", "start": 0.0, "end": 1.0},
                    {"label": "G", "start": 1.0, "end": 2.0},
                ]
            }
        ),
        encoding="utf-8",
    )

    metrics = reference_chord_metrics(reference, metadata)

    assert metrics["available"] is True
    assert metrics["root_wcsr"] == 1.0
    assert metrics["majmin_wcsr"] == 1.0
    assert metrics["triads_wcsr"] == 1.0


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


def test_benchmark_job_dir_scores_reference_chords(tmp_path: Path):
    job_dir = tmp_path / "abcdefabcdef"
    (job_dir / "stems").mkdir(parents=True)
    (job_dir / "metadata.json").write_text(
        json.dumps(
            {
                "chord_progression": [
                    {"label": "C", "start": 0.0, "end": 1.0},
                    {"label": "G", "start": 1.0, "end": 2.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    reference = tmp_path / "reference.lab"
    reference.write_text("0.0\t1.0\tC:maj\n1.0\t2.0\tG:maj\n", encoding="utf-8")

    report = benchmark_job_dir(job_dir, reference_chords_path=reference)

    assert report["chord_reference"]["available"] is True
    assert report["chord_reference"]["triads_wcsr"] == 1.0


def test_benchmark_audio_without_source_reports_chords_only(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    _write_wav(stems_dir / "vocals.wav", np.zeros(16, dtype=np.float32), 8000)

    report = benchmark_audio(source=None, stems_dir=stems_dir, sr=8000, duration=1.0)

    assert report["stem_sum"]["available"] is False
    assert report["stems"]["present"] == ["vocals"]


def test_benchmark_audio_includes_labelled_reference_stem_metrics(tmp_path: Path):
    reference_dir = tmp_path / "reference"
    stems_dir = tmp_path / "stems"
    reference_dir.mkdir()
    stems_dir.mkdir()
    samples = np.linspace(-0.5, 0.5, 8000, dtype=np.float32)
    _write_wav(reference_dir / "vocals.wav", samples, 8000)
    _write_wav(stems_dir / "vocals.wav", samples, 8000)

    report = benchmark_audio(
        source=None,
        stems_dir=stems_dir,
        reference_stems_dir=reference_dir,
        sr=8000,
        duration=1.0,
    )

    assert report["stem_reference"]["available"] is True
    assert report["stem_reference"]["per_stem"]["vocals"]["si_sdr_db"] > 80


def test_benchmark_jobs_root_and_compare_reports(tmp_path: Path):
    sr = 8000
    job_dir = tmp_path / "abcdefabcdef"
    stems_dir = job_dir / "stems"
    stems_dir.mkdir(parents=True)
    samples = np.zeros(sr, dtype=np.float32)
    _write_wav(job_dir / "source.wav", samples, sr)
    _write_wav(stems_dir / "vocals.wav", samples, sr)
    (stems_dir / "chords.mid").write_bytes(b"MThd")
    (job_dir / "metadata.json").write_text(
        json.dumps(
            {
                "duration_sec": 1.0,
                "chord_progression": [
                    {"label": "C", "confidence": 0.9, "start": 0.0, "end": 1.0, "start_beat": 0, "end_beat": 4}
                ],
            }
        ),
        encoding="utf-8",
    )

    report = benchmark_jobs_root(tmp_path, sr=sr, duration=1.0)
    comparison = compare_benchmark_reports(
        report,
        {
            "schema": "layerlab-benchmark-suite-v1",
            "job_count": 1,
            "summary": {"chord_confidence_mean": 0.8, "unstable_short_segment_total": 2},
        },
    )

    assert report["schema"] == "layerlab-benchmark-suite-v1"
    assert report["job_count"] == 1
    assert report["summary"]["chord_confidence_mean"] == 0.9
    assert comparison["summary_delta"]["chord_confidence_mean"] == 0.1
    assert comparison["summary_delta"]["unstable_short_segment_total"] == -2
