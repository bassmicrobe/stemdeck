from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Iterable
from statistics import median

from app.core.models import Job

STAGE_RANGES: dict[str, tuple[float, float]] = {
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
PIPELINE_STAGES = tuple(STAGE_RANGES)

_DEFAULT_DURATION_SECONDS = 240.0
_MAX_STAGE_SECONDS = 24 * 60 * 60.0
_QUALITY_WORKLOAD = {"standard": 1.0, "high": 4.0, "max": 8.0, "ultra": 16.0}
_SEPARATION_REALTIME = {"cuda": 0.14, "mps": 0.22, "cpu": 0.9}


def _clamp01(value: float | int | None) -> float:
    try:
        number = float(value if value is not None else 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def _duration(job: Job) -> float:
    try:
        value = float(job.duration_sec or _DEFAULT_DURATION_SECONDS)
    except (TypeError, ValueError):
        value = _DEFAULT_DURATION_SECONDS
    if not math.isfinite(value):
        value = _DEFAULT_DURATION_SECONDS
    return max(5.0, min(6 * 60 * 60.0, value))


def _device(job: Job) -> str:
    value = (job.demucs_device_resolved or job.demucs_device or "cpu").strip().lower()
    return value if value in _SEPARATION_REALTIME else "cpu"


def _source_kind(job: Job) -> str:
    source = (job.source_url or "").strip().lower()
    return "remote" if source.startswith(("http://", "https://")) else "local"


def _baseline_stage_estimates(job: Job) -> dict[str, float]:
    duration = _duration(job)
    quality = (job.quality_preset or "standard").strip().lower()
    workload = _QUALITY_WORKLOAD.get(quality, 1.0)
    denoise = (job.stem_denoise_preset or "off").strip().lower()
    quality_post_factor = 1.35 if quality != "standard" else 1.0

    sampled_analysis = min(duration, 90.0) * 0.18 + max(0.0, duration - 90.0) * 0.02
    repair_seconds = 0.1 if quality == "standard" else 0.5 + duration * 0.06
    phase_seconds = 0.1 if quality == "standard" else 0.5 + duration * 0.07
    denoise_rate = {"off": 0.0, "light": 0.10, "strong": 0.18}.get(denoise, 0.0)

    return {
        "acquire": 12.0 if _source_kind(job) == "remote" else 0.5,
        "analyze": 2.5 + sampled_analysis,
        "prepare_separation": 0.5,
        "separate": 3.0 + duration * _SEPARATION_REALTIME[_device(job)] * workload,
        "collect": 0.5 + duration * 0.005,
        "restore_gain": 0.2 if quality == "standard" else 0.5 + duration * 0.02,
        "bass_repair": repair_seconds,
        "phase_repair": phase_seconds,
        "denoise": 0.1 if denoise_rate == 0.0 else 0.5 + duration * denoise_rate,
        "gate": (1.0 + duration * 0.30) * quality_post_factor,
        "stabilize": 0.2 + duration * 0.005,
        "presence": 0.2,
        "mix": 0.3 + duration * 0.01,
        "peaks": 0.4 + duration * 0.012,
        "chords": 2.0 + duration * 0.12,
    }


def _valid_seconds(value: object) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or not 0.0 < seconds <= _MAX_STAGE_SECONDS:
        return None
    return seconds


def _valid_timestamp(value: object) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0.0:
        return None
    return timestamp


def _valid_progress_percent(value: object) -> float | None:
    try:
        progress = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(progress) or not 0.0 <= progress <= 100.0:
        return None
    return progress


def _timings_from_logs(job: Job) -> dict[str, float]:
    first_timestamp: dict[str, float] = {}
    valid_entries = [entry for entry in job.logs if isinstance(entry, dict)]
    entries = sorted(
        valid_entries,
        key=lambda item: _valid_timestamp(item.get("timestamp")) or 0.0,
    )
    for entry in entries:
        stage = entry.get("stage")
        timestamp = _valid_timestamp(entry.get("timestamp"))
        if stage not in STAGE_RANGES or timestamp is None or stage in first_timestamp:
            continue
        progress = _valid_progress_percent(entry.get("progress_percent"))
        stage_start_percent = STAGE_RANGES[stage][0] * 100.0
        if progress is not None and progress <= stage_start_percent + 1.1:
            first_timestamp[stage] = timestamp

    parsed: dict[str, float] = {}
    for index, stage in enumerate(PIPELINE_STAGES):
        started_at = first_timestamp.get(stage)
        if started_at is None:
            continue
        ended_at = next(
            (
                first_timestamp[next_stage]
                for next_stage in PIPELINE_STAGES[index + 1 :]
                if first_timestamp.get(next_stage, 0.0) >= started_at
            ),
            job.completed_at,
        )
        if ended_at is None:
            continue
        elapsed = _valid_seconds(float(ended_at) - started_at)
        if elapsed is not None:
            parsed[stage] = elapsed

    progress_samples: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)
    for entry in entries:
        stage = entry.get("stage")
        timestamp = _valid_timestamp(entry.get("timestamp"))
        progress = _valid_progress_percent(entry.get("progress_percent"))
        if stage not in STAGE_RANGES or timestamp is None or progress is None:
            continue
        start, end = STAGE_RANGES[stage]
        local_fraction = _clamp01(((progress / 100.0) - start) / (end - start))
        progress_samples[stage].append((timestamp, local_fraction))

    inferred: dict[str, float] = {}
    for stage, samples in progress_samples.items():
        increasing: list[tuple[float, float]] = []
        for timestamp, fraction in samples:
            if not increasing or fraction > increasing[-1][1] + 0.005:
                increasing.append((timestamp, fraction))
        if len(increasing) < 2:
            continue
        first_time, first_fraction = increasing[0]
        last_time, last_fraction = increasing[-1]
        fraction_span = last_fraction - first_fraction
        if fraction_span < 0.1 or last_time - first_time < 0.5:
            continue
        estimated = _valid_seconds((last_time - first_time) / fraction_span)
        if estimated is not None:
            inferred[stage] = estimated

    recorded = {
        stage: seconds
        for stage, raw in job.stage_timings.items()
        if stage in STAGE_RANGES and (seconds := _valid_seconds(raw)) is not None
    }
    return {**inferred, **parsed, **recorded}


def _robust_median(values: list[float]) -> float:
    center = float(median(values))
    if len(values) < 4:
        return center
    deviations = [abs(value - center) for value in values]
    mad = float(median(deviations))
    if mad <= 1e-9:
        filtered = [value for value in values if value <= center * 3.0]
    else:
        filtered = [value for value in values if abs(value - center) <= mad * 3.5]
    return float(median(filtered or values))


def configure_eta_history(job: Job, completed_jobs: Iterable[Job]) -> None:
    """Attach robust per-stage runtime rates from comparable local jobs."""
    rates: defaultdict[str, list[float]] = defaultdict(list)
    used_jobs = 0
    target_profile = (
        (job.quality_preset or "standard").lower(),
        _device(job),
        (job.stem_denoise_preset or "off").lower(),
        _source_kind(job),
    )
    candidates = sorted(
        completed_jobs,
        key=lambda item: item.completed_at or item.created_at,
        reverse=True,
    )[:40]
    for previous in candidates:
        previous_profile = (
            (previous.quality_preset or "standard").lower(),
            _device(previous),
            (previous.stem_denoise_preset or "off").lower(),
            _source_kind(previous),
        )
        if previous.id == job.id or previous.status != "done" or previous_profile != target_profile:
            continue
        previous_duration = _valid_seconds(previous.duration_sec)
        if previous_duration is None:
            continue
        timings = _timings_from_logs(previous)
        if not timings:
            continue
        used_jobs += 1
        for stage, seconds in timings.items():
            rates[stage].append(seconds / previous_duration)

    job.eta_history_stage_rates = {
        stage: _robust_median(values) for stage, values in rates.items() if values
    }
    job.eta_history_samples = used_jobs
    job.eta_stage_estimates = {}
    job.eta_estimate_duration_sec = None


def _refresh_stage_estimates(job: Job) -> bool:
    duration = _duration(job)
    if job.eta_stage_estimates and job.eta_estimate_duration_sec == duration:
        return False

    estimates = _baseline_stage_estimates(job)
    if job.eta_history_stage_rates:
        history_weight = min(0.92, 0.75 + max(0, job.eta_history_samples - 1) * 0.05)
        for stage, rate in job.eta_history_stage_rates.items():
            if stage not in estimates:
                continue
            historical = max(estimates[stage] * 0.2, min(estimates[stage] * 5.0, rate * duration))
            estimates[stage] = (
                estimates[stage] * (1.0 - history_weight) + historical * history_weight
            )

    job.eta_stage_estimates = estimates
    job.eta_estimate_duration_sec = duration
    return True


def _future_stage_seconds(job: Job, stage_key: str) -> float:
    index = PIPELINE_STAGES.index(stage_key)
    return sum(job.eta_stage_estimates.get(stage, 0.0) for stage in PIPELINE_STAGES[index + 1 :])


def _record_finished_stage(job: Job, now: float) -> None:
    if job.eta_stage_key is None or job.eta_stage_started_at is None:
        return
    elapsed = max(0.0, now - job.eta_stage_started_at)
    if elapsed > 0.0:
        job.stage_timings = {**job.stage_timings, job.eta_stage_key: elapsed}


def _live_seconds_per_fraction(samples: list[tuple[float, float]]) -> float | None:
    effective_samples = samples
    if samples and samples[0][1] <= 0.01:
        if len(samples) < 3:
            return None
        # The first interval includes model/process startup. It is useful for
        # the full-stage baseline but biases the remaining-time slope high.
        effective_samples = samples[1:]
    slopes: list[float] = []
    for (start_time, start_fraction), (end_time, end_fraction) in zip(
        effective_samples, effective_samples[1:], strict=False
    ):
        delta_fraction = end_fraction - start_fraction
        delta_time = end_time - start_time
        if delta_fraction >= 0.005 and delta_time >= 0.05:
            slopes.append(delta_time / delta_fraction)
    if not slopes:
        return None
    first_time, first_fraction = effective_samples[0]
    last_time, last_fraction = effective_samples[-1]
    if last_fraction - first_fraction >= 0.01 and last_time > first_time:
        slopes.append((last_time - first_time) / (last_fraction - first_fraction))
    return _robust_median(slopes)


def _move_deadline(job: Job, candidate: float, now: float) -> None:
    if job.eta_completion_at is None:
        job.eta_completion_at = candidate
        return
    delta = candidate - job.eta_completion_at
    if abs(delta) < 0.5:
        return
    old_remaining = max(0.0, job.eta_completion_at - now)
    max_shift = max(10.0, min(180.0, old_remaining * 0.4 + 10.0))
    bounded_delta = max(-max_shift, min(max_shift, delta))
    job.eta_completion_at += bounded_delta * 0.6


def update_job_eta(job: Job, stage_key: str, fraction: float | int | None) -> None:
    if stage_key not in STAGE_RANGES:
        raise KeyError(f"unknown pipeline progress stage: {stage_key}")
    now = time.time()
    estimates_changed = _refresh_stage_estimates(job)
    local_fraction = _clamp01(fraction)

    if job.eta_stage_key != stage_key:
        _record_finished_stage(job, now)
        job.eta_stage_key = stage_key
        job.eta_stage_started_at = now
        job.eta_stage_fraction = local_fraction
        job.eta_stage_samples = [(now, local_fraction)]
        remaining = job.eta_stage_estimates[stage_key] * (1.0 - local_fraction)
        job.eta_completion_at = now + remaining + _future_stage_seconds(job, stage_key)
        job.eta_method = "history" if job.eta_history_samples else "baseline"
        job.eta_confidence = "medium" if job.eta_history_samples >= 3 else "low"
        return

    previous_fraction = job.eta_stage_fraction
    if local_fraction <= previous_fraction + 1e-6:
        if estimates_changed:
            remaining = job.eta_stage_estimates[stage_key] * (1.0 - previous_fraction)
            job.eta_completion_at = now + remaining + _future_stage_seconds(job, stage_key)
        return

    samples = [*job.eta_stage_samples, (now, local_fraction)][-12:]
    job.eta_stage_samples = samples
    job.eta_stage_fraction = local_fraction
    baseline_remaining = job.eta_stage_estimates[stage_key] * (1.0 - local_fraction)
    current_remaining = baseline_remaining
    live_rate = _live_seconds_per_fraction(samples)
    if live_rate is not None:
        progress_span = max(0.0, samples[-1][1] - samples[0][1])
        live_weight = min(0.85, 0.35 + progress_span * 1.25)
        live_remaining = live_rate * (1.0 - local_fraction)
        current_remaining = baseline_remaining * (1.0 - live_weight) + live_remaining * live_weight
        job.eta_method = "live"
        job.eta_confidence = "high" if progress_span >= 0.25 and len(samples) >= 3 else "medium"

    candidate = now + current_remaining + _future_stage_seconds(job, stage_key)
    _move_deadline(job, candidate, now)


def finalize_job_eta(job: Job) -> None:
    _record_finished_stage(job, time.time())
    job.eta_stage_key = None
    job.eta_stage_started_at = None
    job.eta_stage_samples = []
    job.eta_completion_at = None
