from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import STEM_NAMES, TIMEOUT_ANALYZE, ffmpeg_executable
from app.pipeline.process import background_process_env

_UNSTABLE_CHORD_SUFFIXES = ("dim", "sus2", "sus4", "maj7")
_MIR_EVAL_QUALITY = {
    "": "maj",
    "m": "min",
    "7": "7",
    "maj7": "maj7",
    "m7": "min7",
    "dim": "dim",
    "dim7": "dim7",
    "hdim7": "hdim7",
    "aug": "aug",
    "sus2": "sus2",
    "sus4": "sus4",
    "6": "maj6",
    "m6": "min6",
    "9": "9",
    "maj9": "maj9",
    "m9": "min9",
    "mMaj7": "minmaj7",
}


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


def decode_audio_stereo(
    path: Path,
    *,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> np.ndarray:
    """Decode audio to stereo float32 without summing channels for peak tests."""
    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-ac",
        "2",
        "-ar",
        str(sr),
        "-f",
        "f32le",
    ]
    if duration and duration > 0:
        cmd += ["-t", str(duration)]
    cmd.append("-")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        check=True,
        timeout=TIMEOUT_ANALYZE,
        env=background_process_env(),
    )
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if samples.size == 0 or samples.size % 2:
        raise ValueError(f"decoded audio is empty: {path}")
    return samples.reshape(-1, 2)


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

    source_audio = decode_audio_stereo(source, sr=sr, duration=duration)
    stem_audio = [
        decode_audio_stereo(stems_dir / f"{name}.wav", sr=sr, duration=duration)
        for name in used
    ]
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
    source_flat = source_aligned.reshape(-1)
    stem_sum_flat = stem_sum.reshape(-1)
    denom = float(np.linalg.norm(source_flat) * np.linalg.norm(stem_sum_flat))
    correlation = float(np.dot(source_flat, stem_sum_flat) / denom) if denom > 1e-12 else None
    clipping_samples = int(np.sum(np.abs(stem_sum) > 1.0))

    return {
        "available": True,
        "source": str(source),
        "stems_dir": str(stems_dir),
        "used_stems": used,
        "missing_stems": missing,
        "sample_rate": sr,
        "channels": 2,
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
        "stem_sum_clipping_percent": _round((clipping_samples / stem_sum.size) * 100.0, 5),
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
    beat_lengths: list[float] = []
    second_lengths: list[float] = []
    unstable_short = 0
    for item in segments:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        start_beat = item.get("start_beat")
        end_beat = item.get("end_beat")
        if isinstance(start_beat, int | float) and isinstance(end_beat, int | float):
            beats = max(0.0, float(end_beat) - float(start_beat))
            if beats > 0:
                beat_lengths.append(beats)
                if beats <= 1.01 and label.endswith(_UNSTABLE_CHORD_SUFFIXES):
                    unstable_short += 1
        start = item.get("start")
        end = item.get("end")
        if isinstance(start, int | float) and isinstance(end, int | float):
            seconds = max(0.0, float(end) - float(start))
            if seconds > 0:
                second_lengths.append(seconds)
    beat_times = metadata.get("beat_times") if isinstance(metadata.get("beat_times"), list) else []
    duration = metadata.get("duration_sec")
    if not isinstance(duration, int | float) or duration <= 0:
        duration = sum(second_lengths) if second_lengths else None
    midi_path = stems_dir / "chords.mid" if stems_dir else None
    return {
        "metadata_available": bool(metadata),
        "midi_available": bool(midi_path and midi_path.is_file()),
        "segment_count": len(segments),
        "unique_labels": sorted(set(labels)),
        "label_counts": dict(sorted(Counter(labels).items())),
        "average_confidence": _round(float(np.mean(confidences)) if confidences else None, 3),
        "average_beats_per_segment": _round(float(np.mean(beat_lengths)) if beat_lengths else None, 3),
        "short_segment_count": sum(1 for beats in beat_lengths if beats <= 1.01),
        "unstable_short_segment_count": unstable_short,
        "chord_changes_per_minute": _round(
            ((len(segments) - 1) / max(float(duration), 1e-12)) * 60.0
            if duration and len(segments) > 1
            else None,
            3,
        ),
        "bpm": metadata.get("bpm"),
        "beat_count": len(beat_times),
    }


def _load_lab(path: Path) -> tuple[np.ndarray, list[str]]:
    intervals: list[tuple[float, float]] = []
    labels: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        start, end = float(parts[0]), float(parts[1])
        if end <= start:
            continue
        intervals.append((start, end))
        labels.append(parts[2])
    return np.asarray(intervals, dtype=np.float64), labels


def _mir_eval_label(label: str) -> str:
    from app.pipeline.chords import _canonical_chord_label, _split_chord_label

    canonical = _canonical_chord_label(label)
    root, suffix = _split_chord_label(canonical)
    if root is None:
        return "N"
    suffix = suffix.split("/", 1)[0]
    quality = _MIR_EVAL_QUALITY.get(suffix, "maj")
    pitches = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{pitches[root]}:{quality}"


def reference_chord_metrics(reference_path: Path, metadata_path: Path) -> dict[str, Any]:
    """Score generated chord intervals against a labelled .lab reference."""
    try:
        from mir_eval.chord import evaluate

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        progression = metadata.get("chord_progression")
        if not isinstance(progression, list):
            raise ValueError("metadata has no chord_progression")
        ref_intervals, ref_labels = _load_lab(reference_path)
        est_intervals: list[tuple[float, float]] = []
        est_labels: list[str] = []
        for item in progression:
            if not isinstance(item, dict):
                continue
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
            if end <= start:
                continue
            est_intervals.append((start, end))
            est_labels.append(_mir_eval_label(str(item.get("label") or "N")))
        if not len(ref_intervals) or not est_intervals:
            raise ValueError("reference or estimate has no valid intervals")
        scores = evaluate(
            ref_intervals,
            ref_labels,
            np.asarray(est_intervals, dtype=np.float64),
            est_labels,
        )
        return {
            "available": True,
            "reference": str(reference_path),
            "root_wcsr": _round(float(scores["root"])),
            "majmin_wcsr": _round(float(scores["majmin"])),
            "triads_wcsr": _round(float(scores["triads"])),
            "tetrads_wcsr": _round(float(scores["tetrads"])),
            "oversegmentation": _round(float(scores["overseg"])),
            "undersegmentation": _round(float(scores["underseg"])),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc), "reference": str(reference_path)}


def benchmark_audio(
    *,
    source: Path | None,
    stems_dir: Path,
    metadata_path: Path | None = None,
    reference_chords_path: Path | None = None,
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
    if reference_chords_path is not None and metadata_path is not None:
        metrics["chord_reference"] = reference_chord_metrics(
            reference_chords_path,
            metadata_path,
        )
    return metrics


def benchmark_job_dir(
    job_dir: Path,
    *,
    source: Path | None = None,
    reference_chords_path: Path | None = None,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> dict[str, Any]:
    stems_dir = job_dir / "stems"
    return benchmark_audio(
        source=source or find_job_source(job_dir),
        stems_dir=stems_dir,
        metadata_path=job_dir / "metadata.json",
        reference_chords_path=reference_chords_path,
        sr=sr,
        duration=duration,
    )


def benchmark_jobs_root(
    jobs_root: Path,
    *,
    sr: int = 44100,
    duration: float | None = 180.0,
) -> dict[str, Any]:
    job_dirs = sorted(path for path in jobs_root.iterdir() if path.is_dir())
    reports = []
    for job_dir in job_dirs:
        metadata_path = job_dir / "metadata.json"
        stems_dir = job_dir / "stems"
        if not metadata_path.is_file() and not stems_dir.is_dir():
            continue
        report = benchmark_job_dir(job_dir, sr=sr, duration=duration)
        report["job_id"] = job_dir.name
        reports.append(report)

    residuals = [
        item["stem_sum"].get("residual_percent")
        for item in reports
        if item.get("stem_sum", {}).get("available")
        and isinstance(item["stem_sum"].get("residual_percent"), int | float)
    ]
    chord_confidences = [
        item["chords"].get("average_confidence")
        for item in reports
        if isinstance(item.get("chords", {}).get("average_confidence"), int | float)
    ]
    unstable_counts = [
        item["chords"].get("unstable_short_segment_count", 0)
        for item in reports
        if isinstance(item.get("chords"), dict)
    ]

    return {
        "schema": "layerlab-benchmark-suite-v1",
        "jobs_root": str(jobs_root),
        "job_count": len(reports),
        "summary": {
            "stem_sum_residual_percent_mean": _round(float(np.mean(residuals)) if residuals else None, 3),
            "stem_sum_residual_percent_max": _round(float(np.max(residuals)) if residuals else None, 3),
            "chord_confidence_mean": _round(float(np.mean(chord_confidences)) if chord_confidences else None, 3),
            "unstable_short_segment_total": int(sum(unstable_counts)),
        },
        "jobs": reports,
    }


def compare_benchmark_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    current_summary = current.get("summary", {}) if isinstance(current.get("summary"), dict) else {}
    baseline_summary = baseline.get("summary", {}) if isinstance(baseline.get("summary"), dict) else {}
    keys = sorted(set(current_summary) | set(baseline_summary))
    deltas: dict[str, Any] = {}
    for key in keys:
        cur = current_summary.get(key)
        base = baseline_summary.get(key)
        if isinstance(cur, int | float) and isinstance(base, int | float):
            deltas[key] = _round(float(cur) - float(base), 6)
        else:
            deltas[key] = None
    return {
        "schema": "layerlab-benchmark-compare-v1",
        "baseline_schema": baseline.get("schema"),
        "current_schema": current.get("schema"),
        "baseline_job_count": baseline.get("job_count"),
        "current_job_count": current.get("job_count"),
        "summary_delta": deltas,
    }
