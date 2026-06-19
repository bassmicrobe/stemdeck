from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import STEM_NAMES, TIMEOUT_ANALYZE, ffmpeg_executable


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def _peak(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples)))


def _dbfs(value: float) -> float | None:
    if value <= 1e-12:
        return None
    return 20.0 * math.log10(value)


def decode_audio_mono(path: Path, *, sr: int = 44100, duration: float | None = 180.0) -> np.ndarray:
    """Decode an arbitrary local audio file to mono float32 samples with ffmpeg."""
    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "f32le",
    ]
    if duration and duration > 0:
        cmd += ["-t", str(duration)]
    cmd.append("-")
    proc = subprocess.run(cmd, capture_output=True, check=True, timeout=TIMEOUT_ANALYZE)
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if samples.size == 0:
        raise ValueError(f"decoded audio is empty: {path}")
    return samples


def find_job_source(job_dir: Path) -> Path | None:
    """Return a retained source file for an in-progress or unswept job, if present."""
    for candidate in sorted(job_dir.glob("source.*")):
        if candidate.is_file() and candidate.suffix != ".demucs.wav":
            return candidate
    return None


def present_stems(stems_dir: Path, stem_names: list[str] | None = None) -> list[str]:
    names = stem_names or list(STEM_NAMES)
    return [name for name in names if (stems_dir / f"{name}.wav").is_file()]


def stem_sum_metrics(
    source: Path,
    stems_dir: Path,
    *,
    stem_names: list[str] | None = None,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> dict[str, Any]:
    """Measure how closely the sum of available stems reconstructs the source."""
    names = stem_names or list(STEM_NAMES)
    used = present_stems(stems_dir, names)
    missing = [name for name in names if name not in used]
    if not used:
        return {
            "available": False,
            "reason": "no stem wav files found",
            "used_stems": [],
            "missing_stems": missing,
        }

    source_audio = decode_audio_mono(source, sr=sr, duration=duration)
    stem_audio = [decode_audio_mono(stems_dir / f"{name}.wav", sr=sr, duration=duration) for name in used]
    length = min([len(source_audio), *(len(audio) for audio in stem_audio)])
    if length <= 0:
        return {
            "available": False,
            "reason": "decoded audio has no overlapping samples",
            "used_stems": used,
            "missing_stems": missing,
        }

    source_aligned = source_audio[:length].astype(np.float64, copy=False)
    stem_sum = np.sum(np.stack([audio[:length].astype(np.float64, copy=False) for audio in stem_audio]), axis=0)
    residual = source_aligned - stem_sum

    source_rms = _rms(source_aligned)
    stem_sum_rms = _rms(stem_sum)
    residual_rms = _rms(residual)
    residual_ratio = residual_rms / max(source_rms, 1e-12)
    denom = float(np.linalg.norm(source_aligned) * np.linalg.norm(stem_sum))
    correlation = float(np.dot(source_aligned, stem_sum) / denom) if denom > 1e-12 else None
    clipping_samples = int(np.sum(np.abs(stem_sum) > 1.0))

    return {
        "available": True,
        "source": str(source),
        "stems_dir": str(stems_dir),
        "used_stems": used,
        "missing_stems": missing,
        "sample_rate": sr,
        "duration_sec": _round(length / sr, 3),
        "source_rms": _round(source_rms),
        "stem_sum_rms": _round(stem_sum_rms),
        "residual_rms": _round(residual_rms),
        "residual_ratio": _round(residual_ratio),
        "residual_percent": _round(residual_ratio * 100.0, 3),
        "snr_db": _round(20.0 * math.log10(max(source_rms, 1e-12) / max(residual_rms, 1e-12)), 3),
        "correlation": _round(correlation),
        "source_peak_dbfs": _round(_dbfs(_peak(source_aligned)), 3),
        "stem_sum_peak_dbfs": _round(_dbfs(_peak(stem_sum)), 3),
        "residual_peak_dbfs": _round(_dbfs(_peak(residual)), 3),
        "stem_sum_clipping_samples": clipping_samples,
        "stem_sum_clipping_percent": _round((clipping_samples / length) * 100.0, 5),
    }


def chord_metrics(metadata_path: Path | None, stems_dir: Path | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if metadata_path and metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    progression = metadata.get("chord_progression")
    segments = progression if isinstance(progression, list) else []
    confidences = [
        float(item["confidence"])
        for item in segments
        if isinstance(item, dict) and isinstance(item.get("confidence"), int | float)
    ]
    labels = [
        str(item.get("label"))
        for item in segments
        if isinstance(item, dict) and item.get("label")
    ]
    midi_path = stems_dir / "chords.mid" if stems_dir else None
    return {
        "metadata_available": bool(metadata),
        "midi_available": bool(midi_path and midi_path.is_file()),
        "segment_count": len(segments),
        "unique_labels": sorted(set(labels)),
        "average_confidence": _round(float(np.mean(confidences)) if confidences else None, 3),
        "bpm": metadata.get("bpm"),
        "beat_count": len(metadata.get("beat_times") or []) if isinstance(metadata.get("beat_times"), list) else 0,
    }


def benchmark_audio(
    *,
    source: Path | None,
    stems_dir: Path,
    metadata_path: Path | None = None,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "schema": "layerlab-benchmark-v1",
        "stems": {
            "stems_dir": str(stems_dir),
            "present": present_stems(stems_dir),
            "missing": [name for name in STEM_NAMES if name not in present_stems(stems_dir)],
        },
        "stem_sum": {
            "available": False,
            "reason": "source audio not provided or retained",
        },
        "chords": chord_metrics(metadata_path, stems_dir),
    }
    if source is not None:
        metrics["stem_sum"] = stem_sum_metrics(source, stems_dir, sr=sr, duration=duration)
    return metrics


def benchmark_job_dir(
    job_dir: Path,
    *,
    source: Path | None = None,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> dict[str, Any]:
    stems_dir = job_dir / "stems"
    return benchmark_audio(
        source=source or find_job_source(job_dir),
        stems_dir=stems_dir,
        metadata_path=job_dir / "metadata.json",
        sr=sr,
        duration=duration,
    )
