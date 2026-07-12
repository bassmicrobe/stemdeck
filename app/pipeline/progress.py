from __future__ import annotations

from app.core.joblog import add_job_log
from app.core.models import Job, JobStatus, _set

# Overall job progress bands. Individual tools such as yt-dlp and Demucs report
# their own 0-100%, but the UI needs a single monotonic timeline for the whole
# pipeline so users can tell when the job is actually close to done.
_STAGE_RANGES: dict[str, tuple[float, float]] = {
    "acquire": (0.02, 0.12),
    "analyze": (0.12, 0.24),
    "prepare_separation": (0.24, 0.30),
    "separate": (0.30, 0.82),
    "collect": (0.82, 0.84),
    "restore_gain": (0.84, 0.86),
    "bass_repair": (0.86, 0.89),
    "phase_repair": (0.89, 0.92),
    "denoise": (0.92, 0.95),
    "gate": (0.95, 0.96),
    "stabilize": (0.96, 0.97),
    "presence": (0.97, 0.978),
    "mix": (0.978, 0.985),
    "peaks": (0.985, 0.99),
    "chords": (0.99, 0.995),
}


def _clamp01(value: float | int | None) -> float:
    try:
        v = float(value if value is not None else 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def set_stage_progress(
    job: Job,
    stage_key: str,
    fraction: float | int | None = 0.0,
    *,
    status: JobStatus | None = None,
    stage: str | None = None,
) -> None:
    """Set monotonic overall progress for a pipeline stage.

    `fraction` is local to the stage (0.0-1.0). It is mapped into the fixed
    whole-pipeline band above, then clamped so later stages never make the
    progress bar jump backwards.
    """
    if stage_key not in _STAGE_RANGES:
        raise KeyError(f"unknown pipeline progress stage: {stage_key}")
    start, end = _STAGE_RANGES[stage_key]
    overall = start + ((end - start) * _clamp01(fraction))
    fields: dict[str, object] = {"progress": max(float(job.progress or 0.0), overall)}
    if status is not None:
        fields["status"] = status
    if stage is not None:
        fields["stage"] = stage
    _set(job, **fields)
    if stage is not None:
        add_job_log(
            job,
            stage,
            stage=stage_key,
            progress=overall,
        )
