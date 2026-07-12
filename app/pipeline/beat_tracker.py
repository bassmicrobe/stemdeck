from __future__ import annotations

import importlib.util
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import BEAT_THIS_MODEL, BEAT_TRACKER, TIMEOUT_ANALYZE, ffmpeg_executable
from app.core.models import Job, JobCancelled
from app.pipeline.process import run_tracked_process

logger = logging.getLogger("stemdeck.beat_tracker")


@dataclass(frozen=True)
class BeatGrid:
    beats: list[float]
    downbeats: list[float]
    engine: str
    model: str
    analysis_source: Path | None = None


_cache_lock = threading.Lock()
_trackers: dict[tuple[str, str], tuple[object, threading.Lock]] = {}


def beat_this_available() -> bool:
    return importlib.util.find_spec("beat_this") is not None


def beat_this_model_for_quality(quality_preset: str | None) -> str:
    if BEAT_THIS_MODEL:
        return BEAT_THIS_MODEL
    return "final0"


def should_use_beat_this(quality_preset: str | None) -> bool:
    if BEAT_TRACKER == "librosa":
        return False
    if BEAT_TRACKER == "beat_this":
        return True
    return True


def _device_for_job(job: Job) -> str:
    # CUDA is well-supported by Beat This!. Keep Apple GPU jobs on CPU because
    # some PyTorch/MPS combinations still lack operators used by the model.
    return "cuda" if job.demucs_device_resolved == "cuda" else "cpu"


def _tracker(model: str, device: str) -> tuple[object, threading.Lock]:
    key = (model, device)
    with _cache_lock:
        cached = _trackers.get(key)
        if cached is not None:
            return cached
        from beat_this.inference import File2Beats

        logger.info("loading Beat This! model %s on %s", model, device)
        instance = File2Beats(
            checkpoint_path=model,
            device=device,
            float16=device == "cuda",
            dbn=False,
        )
        cached = (instance, threading.Lock())
        _trackers[key] = cached
        return cached


def _prepare_tracker_source(job: Job, source: Path) -> Path:
    """Create a compact PCM cache for containers Beat This! cannot decode."""
    if source.suffix.lower() in {".wav", ".flac", ".mp3", ".ogg"}:
        return source
    prepared = source.with_suffix(".beats.wav")
    if prepared.is_file():
        return prepared
    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ar",
        "22050",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(prepared),
    ]
    result = run_tracked_process(job, cmd, timeout=TIMEOUT_ANALYZE)
    if result.returncode != 0 or not prepared.is_file():
        prepared.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not prepare beat analysis audio: {detail}")
    return prepared


def detect_beat_grid(job: Job, source: Path) -> BeatGrid | None:
    """Run the optional neural beat/downbeat tracker with safe fallback."""
    if not should_use_beat_this(job.quality_preset) or not beat_this_available():
        return None
    if job.cancel_requested:
        raise JobCancelled()

    model = beat_this_model_for_quality(job.quality_preset)
    device = _device_for_job(job)
    try:
        analysis_source = _prepare_tracker_source(job, source)
        tracker, inference_lock = _tracker(model, device)
        with inference_lock:
            if job.cancel_requested:
                raise JobCancelled()
            beats_raw, downbeats_raw = tracker(str(analysis_source))  # type: ignore[operator]
        beats = sorted(
            {
                round(float(value), 3)
                for value in beats_raw
                if float(value) >= 0 and float(value) <= float(job.duration_sec or 1e12)
            }
        )
        downbeats = sorted(
            {
                round(float(value), 3)
                for value in downbeats_raw
                if float(value) >= 0 and float(value) <= float(job.duration_sec or 1e12)
            }
        )
        if len(beats) < 3:
            logger.warning("Beat This! returned too few beats for job %s", job.id)
            return None
        logger.info(
            "Beat This! detected %s beats and %s downbeats for job %s",
            len(beats),
            len(downbeats),
            job.id,
        )
        return BeatGrid(
            beats=beats,
            downbeats=downbeats,
            engine="beat_this",
            model=model,
            analysis_source=analysis_source,
        )
    except JobCancelled:
        raise
    except Exception:
        logger.warning(
            "Beat This! unavailable for job %s; falling back to librosa",
            job.id,
            exc_info=True,
        )
        return None
