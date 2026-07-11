from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import DATA_DIR, PCM_WORKER_ENABLED, PCM_WORKER_TIMEOUT, ROOT
from app.core.joblog import add_job_log
from app.core.models import Job, JobCancelled
from app.pipeline.process import run_tracked_process

logger = logging.getLogger("stemdeck.pcm_worker")


@dataclass(frozen=True)
class PcmFileResult:
    path: str
    output: str
    changed: bool


@dataclass(frozen=True)
class PcmAnalysis:
    path: str
    sample_rate: int
    channels: int
    duration_seconds: float
    peak: float
    rms: float
    waveform_peaks: tuple[float, ...]
    min_max_peaks: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class PcmResponse:
    engine: str
    files: tuple[PcmFileResult, ...]
    analyses: tuple[PcmAnalysis, ...]


def mark_python_pcm_fallback(job: Job) -> None:
    if job.pcm_engine and "python-soundfile" not in job.pcm_engine:
        job.pcm_engine = f"{job.pcm_engine}+python-soundfile-fallback"
    elif not job.pcm_engine:
        job.pcm_engine = "python-soundfile"


def mark_rust_pcm(job: Job, engine: str) -> None:
    if "python-soundfile" in job.pcm_engine:
        job.pcm_engine = f"{engine}+python-soundfile-fallback"
    else:
        job.pcm_engine = engine


def _is_executable(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def pcm_worker_executable() -> Path | None:
    """Locate the optional Rust PCM sidecar without requiring it in dev."""
    executable_name = "layerlab-pcm.exe" if os.name == "nt" else "layerlab-pcm"
    explicit = os.environ.get("LAYERLAB_PCM_WORKER", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if _is_executable(path):
            return path
        logger.warning("configured Rust PCM worker is unavailable: %s", path)
        return None

    candidates = (
        DATA_DIR / "runtime" / "bin" / executable_name,
        ROOT / "bin" / executable_name,
        ROOT / "desktop" / "src-tauri" / "target" / "release" / executable_name,
        ROOT / "desktop" / "src-tauri" / "target" / "debug" / executable_name,
    )
    for candidate in candidates:
        if _is_executable(candidate):
            return candidate.resolve()

    discovered = shutil.which("layerlab-pcm")
    if discovered:
        path = Path(discovered).resolve()
        if _is_executable(path):
            return path
    return None


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"PCM response field {field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"PCM response field {field} must be finite")
    return result


def _parse_file_result(value: Any) -> PcmFileResult:
    if not isinstance(value, dict):
        raise ValueError("PCM file result must be an object")
    path = value.get("path")
    output = value.get("output")
    changed = value.get("changed")
    if not isinstance(path, str) or not path:
        raise ValueError("PCM file result path is invalid")
    if not isinstance(output, str) or not output:
        raise ValueError("PCM file result output is invalid")
    if not isinstance(changed, bool):
        raise ValueError("PCM file result changed flag is invalid")
    return PcmFileResult(path, output, changed)


def _parse_analysis(value: Any) -> PcmAnalysis:
    if not isinstance(value, dict):
        raise ValueError("PCM analysis must be an object")
    path = value.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("PCM analysis path is invalid")
    waveform_raw = value.get("waveformPeaks", [])
    min_max_raw = value.get("minMaxPeaks", [])
    if not isinstance(waveform_raw, list) or not isinstance(min_max_raw, list):
        raise ValueError("PCM analysis peaks are invalid")
    waveform = tuple(_number(item, "waveformPeaks") for item in waveform_raw)
    min_max: list[tuple[float, float]] = []
    for item in min_max_raw:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("PCM analysis minMaxPeaks entry is invalid")
        min_max.append((_number(item[0], "minMaxPeaks"), _number(item[1], "minMaxPeaks")))
    sample_rate = int(_number(value.get("sampleRate"), "sampleRate"))
    channels = int(_number(value.get("channels"), "channels"))
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("PCM analysis format is invalid")
    return PcmAnalysis(
        path=path,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=_number(value.get("durationSeconds"), "durationSeconds"),
        peak=_number(value.get("peak"), "peak"),
        rms=_number(value.get("rms"), "rms"),
        waveform_peaks=waveform,
        min_max_peaks=tuple(min_max),
    )


def _parse_response(stdout: bytes) -> PcmResponse:
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Rust PCM worker returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Rust PCM worker response must be an object")
    if value.get("ok") is not True:
        message = value.get("error")
        raise ValueError(str(message) if message else "Rust PCM worker rejected the command")
    engine = value.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("Rust PCM worker response has no engine identifier")
    files_raw = value.get("files", [])
    analyses_raw = value.get("analyses", [])
    if not isinstance(files_raw, list) or not isinstance(analyses_raw, list):
        raise ValueError("Rust PCM worker response collections are invalid")
    return PcmResponse(
        engine=engine.strip(),
        files=tuple(_parse_file_result(item) for item in files_raw),
        analyses=tuple(_parse_analysis(item) for item in analyses_raw),
    )


def run_pcm_command(
    job: Job,
    payload: dict[str, Any],
    *,
    timeout: float = PCM_WORKER_TIMEOUT,
) -> PcmResponse | None:
    """Execute one Rust PCM command, returning None for Python fallback."""
    if not PCM_WORKER_ENABLED:
        return None
    executable = pcm_worker_executable()
    if executable is None:
        logger.debug("Rust PCM worker not installed; using Python PCM path")
        if "python-soundfile" not in job.pcm_engine:
            add_job_log(
                job,
                "Rust PCM worker unavailable; using Python audio post-processing",
                level="warning",
                stage="pcm",
            )
        return None
    try:
        input_data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        result = run_tracked_process(
            job,
            [str(executable)],
            timeout=timeout,
            input_data=input_data,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = result.stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"worker exited with {result.returncode}: {detail[-500:]}")
        response = _parse_response(result.stdout)
        mark_rust_pcm(job, response.engine)
        return response
    except JobCancelled:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        logger.warning("Rust PCM command failed; using Python fallback", exc_info=True)
        add_job_log(
            job,
            f"Rust PCM command failed; using Python fallback: {error}",
            level="warning",
            stage="pcm",
        )
        return None
