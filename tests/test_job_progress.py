from __future__ import annotations

import time

from app.core.models import Job, _set
from app.pipeline.progress import set_stage_progress


def test_job_state_includes_progress_percent_and_eta():
    job = Job(id="abcdefabcdef", status="separating", progress=0.5)
    job.status_started_at = time.time() - 20

    state = job.to_state()

    assert state["progress_percent"] == 50
    assert state["elapsed_seconds"] >= 19
    assert state["total_elapsed_seconds"] >= 0
    assert 18 <= state["eta_seconds"] <= 22


def test_job_eta_resets_on_status_change_and_hides_when_done():
    job = Job(id="abcdefabcdef", status="queued", progress=0.0)
    old_started_at = job.status_started_at
    time.sleep(0.01)

    _set(job, status="separating", progress=0.0)

    assert job.status_started_at > old_started_at
    assert job.to_state()["eta_seconds"] is None

    _set(job, status="done", progress=1.0)

    state = job.to_state()
    assert state["progress_percent"] == 100
    assert state["eta_seconds"] is None


def test_job_eta_uses_overall_progress_clock_after_status_change():
    job = Job(id="abcdefabcdef", status="processing", progress=0.5)
    job.progress_started_at = time.time() - 20
    job.status_started_at = time.time() - 2

    eta = job.to_state()["eta_seconds"]

    assert eta is not None
    assert 18 <= eta <= 22


def test_pipeline_stage_progress_is_overall_and_monotonic():
    job = Job(id="abcdefabcdef")

    set_stage_progress(job, "acquire", 1.0, status="downloading", stage="Download complete")
    assert job.to_state()["progress_percent"] == 12

    set_stage_progress(job, "analyze", 0.0, status="analyzing", stage="Analyzing")
    assert job.to_state()["progress_percent"] == 12

    set_stage_progress(job, "analyze", 1.0, stage="Analysis complete")
    assert job.to_state()["progress_percent"] == 24

    set_stage_progress(job, "separate", 0.0, status="separating", stage="Separating stems")
    assert job.to_state()["progress_percent"] == 30

    set_stage_progress(job, "separate", 1.0, stage="Separating 100%")
    assert job.to_state()["progress_percent"] == 82

    # Post-processing starts at local 0%, but the overall bar must not jump
    # back to 0 after Demucs finishes.
    set_stage_progress(job, "collect", 0.0, status="processing", stage="Collecting stems")
    assert job.to_state()["progress_percent"] == 82


def test_job_state_includes_repair_metrics():
    job = Job(
        id="abcdefabcdef",
        bass_repair_applied=True,
        phase_repair_applied=True,
        phase_repair_residual_ratio=0.37,
        stem_denoise_preset="light",
        stem_denoise_applied=True,
    )

    state = job.to_state()

    assert state["bass_repair_applied"] is True
    assert state["phase_repair_applied"] is True
    assert state["phase_repair_residual_ratio"] == 0.37
    assert state["stem_denoise_preset"] == "light"
    assert state["stem_denoise_applied"] is True
