from __future__ import annotations

from app.core.joblog import add_job_log
from app.core.models import Job, JobStatus, _set
from app.pipeline.eta import STAGE_RANGES, update_job_eta


# Overall job progress bands. Individual tools such as yt-dlp and Demucs report
# their own 0-100%, but the UI needs a single monotonic timeline for the whole
# pipeline so users can tell when the job is actually close to done.
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
    if stage_key not in STAGE_RANGES:
        raise KeyError(f"unknown pipeline progress stage: {stage_key}")
    local_fraction = _clamp01(fraction)
    start, end = STAGE_RANGES[stage_key]
    overall = start + ((end - start) * local_fraction)
    fields: dict[str, object] = {"progress": max(float(job.progress or 0.0), overall)}
    if status is not None:
        fields["status"] = status
    if stage is not None:
        fields["stage"] = stage
    _set(job, **fields)
    update_job_eta(job, stage_key, local_fraction)
    if stage is not None:
        add_job_log(
            job,
            stage,
            stage=stage_key,
            progress=overall,
        )
