from __future__ import annotations

import pytest

import app.core.models as models_mod
import app.pipeline.eta as eta_mod
from app.core.models import Job
from app.pipeline.eta import configure_eta_history
from app.pipeline.progress import set_stage_progress


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    value = _Clock()
    monkeypatch.setattr(eta_mod.time, "time", value.time)
    monkeypatch.setattr(models_mod.time, "time", value.time)
    return value


def _job(**overrides: object) -> Job:
    values = {
        "id": "abcdefabcdef",
        "status": "processing",
        "duration_sec": 300.0,
        "quality_preset": "standard",
        "stem_denoise_preset": "off",
        "demucs_device_resolved": "mps",
    }
    values.update(overrides)
    return Job(**values)


def test_static_progress_counts_down_without_moving_deadline(clock: _Clock) -> None:
    job = _job()
    set_stage_progress(job, "separate", 0.2, status="separating", stage="Separating 20%")
    deadline = job.eta_completion_at
    initial_eta = job.eta_seconds()

    clock.advance(15)
    set_stage_progress(job, "separate", 0.2, stage="Separating 20%")

    assert deadline is not None
    assert initial_eta is not None
    assert job.eta_completion_at == deadline
    assert job.eta_seconds() == pytest.approx(initial_eta - 15)


def test_live_stage_rate_recalibrates_forecast(clock: _Clock) -> None:
    job = _job()
    set_stage_progress(job, "separate", 0.0, status="separating", stage="Separating")

    clock.advance(20)
    set_stage_progress(job, "separate", 0.2, stage="Separating 20%")
    assert job.eta_method == "baseline"
    clock.advance(20)
    set_stage_progress(job, "separate", 0.4, stage="Separating 40%")

    state = job.to_state()
    assert state["eta_method"] == "live"
    assert state["eta_confidence"] in {"medium", "high"}
    assert state["eta_completion_at"] is not None
    assert state["eta_seconds"] is not None
    assert 60 < state["eta_seconds"] < 300


def test_stage_transition_records_timing_for_future_jobs(clock: _Clock) -> None:
    job = _job()
    set_stage_progress(job, "analyze", 0.0, status="analyzing", stage="Analyzing")
    clock.advance(12)
    set_stage_progress(job, "separate", 0.0, status="separating", stage="Separating")

    assert job.stage_timings["analyze"] == pytest.approx(12)


def test_matching_history_calibrates_stage_estimates(clock: _Clock) -> None:
    baseline = _job(id="111111111111", duration_sec=200.0, quality_preset="max")
    set_stage_progress(baseline, "separate", 0.0, status="separating", stage="Separating")
    baseline_seconds = baseline.eta_stage_estimates["separate"]

    previous = _job(
        id="222222222222",
        status="done",
        duration_sec=100.0,
        quality_preset="max",
        stage_timings={"separate": 300.0, "analyze": 25.0},
    )
    target = _job(id="333333333333", duration_sec=200.0, quality_preset="max")
    configure_eta_history(target, [previous])
    set_stage_progress(target, "separate", 0.0, status="separating", stage="Separating")

    assert target.eta_history_samples == 1
    assert target.eta_stage_estimates["separate"] > baseline_seconds
    assert target.to_state()["eta_method"] == "history"


def test_legacy_log_timestamps_are_used_as_history(clock: _Clock) -> None:
    previous = _job(
        id="444444444444",
        status="done",
        duration_sec=100.0,
        completed_at=1_700_000_050.0,
        logs=[
            {"stage": "analyze", "timestamp": 1_700_000_000.0, "progress_percent": 12},
            {"stage": "separate", "timestamp": 1_700_000_010.0, "progress_percent": 30},
            {"stage": "collect", "timestamp": 1_700_000_040.0, "progress_percent": 82},
        ],
    )
    target = _job(id="555555555555", duration_sec=200.0)

    configure_eta_history(target, [previous])

    assert target.eta_history_samples == 1
    assert target.eta_history_stage_rates["analyze"] == pytest.approx(0.1)
    assert target.eta_history_stage_rates["separate"] == pytest.approx(0.3)


def test_truncated_legacy_stage_uses_progress_slope(clock: _Clock) -> None:
    previous = _job(
        id="777777777777",
        status="done",
        duration_sec=100.0,
        completed_at=1_700_000_050.0,
        logs=[
            {"stage": "separate", "timestamp": 1_700_000_010.0, "progress_percent": 56},
            {"stage": "separate", "timestamp": 1_700_000_030.0, "progress_percent": 69},
            {"stage": "collect", "timestamp": 1_700_000_040.0, "progress_percent": 82},
        ],
    )
    target = _job(id="888888888888", duration_sec=200.0)

    configure_eta_history(target, [previous])

    assert target.eta_history_stage_rates["separate"] == pytest.approx(0.8)


def test_expired_forecast_switches_to_recalibrating(clock: _Clock) -> None:
    job = _job()
    set_stage_progress(job, "separate", 0.5, status="separating", stage="Separating 50%")
    assert job.eta_completion_at is not None

    clock.now = job.eta_completion_at + 1
    state = job.to_state()

    assert state["eta_seconds"] is None
    assert state["eta_status"] == "recalibrating"


def test_job_record_discards_malformed_stage_timings() -> None:
    restored = Job.from_record(
        {
            "id": "666666666666",
            "status": "done",
            "stage_timings": ["not", "a", "mapping"],
        }
    )

    assert restored.stage_timings == {}
