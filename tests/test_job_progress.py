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


def test_processing_timer_starts_independently_from_progress_updates():
    job = Job(id="abcdefabcdef", status="queued", progress=0.0)

    _set(job, status="processing", stage="Waiting for audio worker...")

    assert job.processing_started_at is not None
    assert job.progress_started_at is None
    assert job.to_state()["processing_elapsed_seconds"] >= 0


def test_terminal_job_elapsed_time_is_frozen():
    job = Job(id="abcdefabcdef", status="processing", progress=0.5)
    job.progress_started_at = time.time() - 20

    _set(job, status="done", progress=1.0)
    first = job.to_state()["processing_elapsed_seconds"]
    time.sleep(0.01)
    second = job.to_state()["processing_elapsed_seconds"]

    assert job.completed_at is not None
    assert first == second
    assert 19 <= first <= 21


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

    set_stage_progress(job, "gate", 1.0, stage="Stem gate complete")
    assert job.to_state()["progress_percent"] == 96
    assert job.logs[-1]["message"] == "Stem gate complete"
    assert job.logs[-1]["progress_percent"] == 96


def test_job_state_includes_repair_metrics():
    job = Job(
        id="abcdefabcdef",
        bass_repair_applied=True,
        phase_repair_applied=True,
        phase_repair_residual_ratio=0.37,
        stem_denoise_preset="light",
        stem_denoise_applied=True,
        stem_gate_applied=True,
        stem_gate_threshold_db=-54.0,
    )

    state = job.to_state()

    assert state["bass_repair_applied"] is True
    assert state["phase_repair_applied"] is True
    assert state["phase_repair_residual_ratio"] == 0.37
    assert state["stem_denoise_preset"] == "light"
    assert state["stem_denoise_applied"] is True
    assert state["stem_gate_applied"] is True
    assert state["stem_gate_threshold_db"] == -54.0


def test_job_state_includes_detected_beat_times():
    progression = [{"label": "C", "start": 0.0, "end": 2.0, "confidence": 0.9}]
    job = Job(
        id="abcdefabcdef",
        bpm=128,
        tempo_stability=92,
        beat_times=[0.511, 0.976, 1.44],
        downbeat_times=[0.511],
        beat_tracker="beat_this:small0",
        chord_progression=progression,
        chord_midi_url="/api/jobs/abcdefabcdef/chords.mid",
        midi_analysis_url="/api/jobs/abcdefabcdef/midi-analysis.json",
    )

    state = job.to_state()

    assert state["bpm"] == 128
    assert state["tempo_stability"] == 92
    assert state["beat_times"] == [0.511, 0.976, 1.44]
    assert state["downbeat_times"] == [0.511]
    assert state["beat_tracker"] == "beat_this:small0"
    assert state["chord_progression"] == progression
    assert state["chord_midi_url"] == "/api/jobs/abcdefabcdef/chords.mid"
    assert state["midi_analysis_url"] == "/api/jobs/abcdefabcdef/midi-analysis.json"


def test_job_state_includes_profile_identity():
    job = Job(
        id="abcdefabcdef",
        selected_stems=["vocals", "bass"],
        quality_preset="max",
        stem_denoise_preset="strong",
        demucs_device="mps",
        demucs_device_resolved="mps",
    )

    state = job.to_state()

    assert state["profile_key"] == "quality=max|denoise=strong|device=mps:mps|stems=vocals,bass"
    assert state["profile_label"] == "Max / Strong denoise / Apple GPU / Vocals+Bass"
