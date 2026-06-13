from __future__ import annotations

import time

from app.core.models import Job, _set


def test_job_state_includes_progress_percent_and_eta():
    job = Job(id="abcdefabcdef", status="separating", progress=0.5)
    job.status_started_at = time.time() - 20

    state = job.to_state()

    assert state["progress_percent"] == 50
    assert state["elapsed_seconds"] >= 19
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
