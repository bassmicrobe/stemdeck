from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path

from app.core.config import JOB_ID_RE, STEM_NAMES
from app.core.files import atomic_write_text
from app.core.models import Job

logger = logging.getLogger("stemdeck.registry")

REGISTRY_VERSION = 1

_jobs: dict[str, Job] = {}
# Active subprocesses keyed by job_id and process identity. Some analysis
# stages run independent FFmpeg decoders concurrently, so cancellation must
# retain every child rather than only the most recently started one.
_procs: dict[str, dict[int, subprocess.Popen]] = {}
_lock = threading.Lock()
_persist_lock = threading.Lock()
_REGISTRY_FILE = "registry.json"
_TERMINAL = {"done", "error", "cancelled"}
_ACTIVE_STATUSES = {"queued", "downloading", "analyzing", "separating", "processing"}


def _refresh_queue_positions_locked() -> None:
    queued = sorted(
        (job for job in _jobs.values() if job.status == "queued" and not job.cancel_requested),
        key=lambda item: item.created_at,
    )
    queue_size = len(queued)
    queued_ids = {job.id for job in queued}
    for index, job in enumerate(queued, start=1):
        job.queue_position = index
        job.queue_size = queue_size
    for job in _jobs.values():
        if job.id not in queued_ids:
            job.queue_position = None
            job.queue_size = queue_size


def refresh_queue_positions() -> None:
    with _lock:
        _refresh_queue_positions_locked()


def register(job: Job) -> Job:
    with _lock:
        _jobs[job.id] = job
        _refresh_queue_positions_locked()
    return job


def register_if_capacity(job: Job, max_pending: int) -> bool:
    """Atomically check active count and register if under capacity.
    Returns True if registered, False if the queue is full."""
    with _lock:
        active = sum(
            1 for j in _jobs.values() if j.status in _ACTIVE_STATUSES and not j.audio_ready
        )
        if active >= max_pending:
            return False
        _jobs[job.id] = job
        _refresh_queue_positions_locked()
    return True


def get(job_id: str) -> Job | None:
    with _lock:
        _refresh_queue_positions_locked()
        return _jobs.get(job_id)


def remove(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)
        _procs.pop(job_id, None)


def all_jobs() -> dict[str, Job]:
    """Return a snapshot of the registry for sweep / cleanup."""
    with _lock:
        _refresh_queue_positions_locked()
        return dict(_jobs)


def _migrate(data: dict) -> dict:
    """Upgrade registry JSON to REGISTRY_VERSION incrementally.
    Each block transforms v(n) → v(n+1) so older snapshots always catch up."""
    version = data.get("version", 0)
    if version < 1:
        # v0 → v1: version field was absent; no structural change needed.
        data["version"] = 1
        version = 1
    # Future migrations go here as `if version < N:` blocks.
    return data


def persist(jobs_dir: Path) -> None:
    """Persist terminal jobs so completed library entries survive restarts."""
    try:
        jobs_dir.mkdir(parents=True, exist_ok=True)
        path = jobs_dir / _REGISTRY_FILE
        with _persist_lock:
            with _lock:
                records = [
                    job.to_record()
                    for job in sorted(_jobs.values(), key=lambda item: item.created_at)
                    if job.status in _TERMINAL
                ]
                payload = json.dumps({"version": REGISTRY_VERSION, "jobs": records}, indent=2) + "\n"
            atomic_write_text(path, payload)
    except (OSError, TypeError, ValueError):
        logger.warning("cannot persist registry under %s", jobs_dir, exc_info=True)


def restore(jobs_dir: Path) -> None:
    """Load persisted jobs and recover completed orphan jobs from disk."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / _REGISTRY_FILE
    if path.is_file():
        try:
            data = _migrate(json.loads(path.read_text(encoding="utf-8")))
            to_add = {}
            for record in data.get("jobs", []):
                job = Job.from_record(record)
                if JOB_ID_RE.match(job.id) and job.status in _TERMINAL:
                    to_add[job.id] = job
            with _lock:
                _jobs.update(to_add)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("failed to load registry from %s", path, exc_info=True)

    with _lock:
        known = set(_jobs)
    changed = False
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir() or not JOB_ID_RE.match(job_dir.name) or job_dir.name in known:
            continue
        recovered = _recover_done_job(job_dir)
        if recovered is not None:
            with _lock:
                _jobs[recovered.id] = recovered
            changed = True
    if changed:
        persist(jobs_dir)


def _recover_done_job(job_dir: Path) -> Job | None:
    stems_dir = job_dir / "stems"
    if not stems_dir.is_dir():
        return None
    stems = [
        {"name": name, "url": f"/api/jobs/{job_dir.name}/stems/{name}.wav"}
        for name in ("original", *STEM_NAMES)
        if (stems_dir / f"{name}.wav").is_file()
    ]
    if not stems:
        return None
    mix_url = None
    if (stems_dir / "mix.wav").is_file():
        mix_url = f"/api/jobs/{job_dir.name}/stems/mix.wav"
    chord_midi_url = None
    if (stems_dir / "chords.mid").is_file():
        chord_midi_url = f"/api/jobs/{job_dir.name}/chords.mid"
    midi_analysis_url = None
    if (stems_dir / "midi-analysis.json").is_file():
        midi_analysis_url = f"/api/jobs/{job_dir.name}/midi-analysis.json"
    selected = [stem["name"] for stem in stems if stem["name"] in STEM_NAMES] or list(STEM_NAMES)
    meta_path = job_dir / "metadata.json"
    if not meta_path.is_file():
        return None
    meta: dict = {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return Job(
        id=job_dir.name,
        status="done",
        progress=1.0,
        stage_message="Done",
        stems=stems,
        selected_stems=meta.get("selected_stems") or selected,
        quality_preset=str(meta.get("quality_preset") or "standard"),
        stem_denoise_preset=str(meta.get("stem_denoise_preset") or "off"),
        demucs_device=str(meta.get("demucs_device") or "auto"),
        demucs_device_resolved=str(meta.get("demucs_device_resolved") or ""),
        demucs_engine=str(meta.get("demucs_engine") or ""),
        pcm_engine=str(meta.get("pcm_engine") or ""),
        mix_url=mix_url,
        created_at=job_dir.stat().st_mtime,
        title=meta.get("title"),
        thumbnail=meta.get("thumbnail"),
        duration_sec=meta.get("duration_sec"),
        bpm=meta.get("bpm"),
        key=meta.get("key"),
        scale=meta.get("scale"),
        key_confidence=meta.get("key_confidence"),
        lufs=meta.get("lufs"),
        peak_db=meta.get("peak_db"),
        dynamic_range=meta.get("dynamic_range"),
        tempo_stability=meta.get("tempo_stability"),
        beat_times=meta.get("beat_times") if isinstance(meta.get("beat_times"), list) else None,
        downbeat_times=meta.get("downbeat_times")
        if isinstance(meta.get("downbeat_times"), list)
        else None,
        beat_tracker=meta.get("beat_tracker"),
        chord_progression=meta.get("chord_progression")
        if isinstance(meta.get("chord_progression"), list)
        else None,
        chord_midi_url=meta.get("chord_midi_url") or chord_midi_url,
        midi_analysis=meta.get("midi_analysis")
        if isinstance(meta.get("midi_analysis"), dict)
        else None,
        midi_analysis_url=meta.get("midi_analysis_url") or midi_analysis_url,
        stem_presence=meta.get("stem_presence"),
        sections=meta.get("sections"),
        tags=meta.get("tags"),
        source_url=meta.get("source_url"),
        bass_repair_applied=bool(meta.get("bass_repair_applied", False)),
        phase_repair_applied=bool(meta.get("phase_repair_applied", False)),
        phase_repair_residual_ratio=meta.get("phase_repair_residual_ratio"),
        stem_denoise_applied=bool(meta.get("stem_denoise_applied", False)),
        stem_gate_applied=bool(meta.get("stem_gate_applied", False)),
        stem_gate_threshold_db=meta.get("stem_gate_threshold_db"),
        processing_started_at=meta.get("processing_started_at"),
        completed_at=meta.get("completed_at"),
        processing_elapsed_seconds=meta.get("processing_elapsed_seconds"),
        logs=meta.get("logs") if isinstance(meta.get("logs"), list) else [],
    )


def add_proc(job_id: str, proc: subprocess.Popen) -> None:
    with _lock:
        active = _procs.setdefault(job_id, {})
        active[id(proc)] = proc


def remove_proc(job_id: str, proc: subprocess.Popen) -> None:
    with _lock:
        active = _procs.get(job_id)
        if active is None:
            return
        active.pop(id(proc), None)
        if not active:
            _procs.pop(job_id, None)


def get_procs(job_id: str) -> tuple[subprocess.Popen, ...]:
    with _lock:
        return tuple(_procs.get(job_id, {}).values())


def get_proc(job_id: str) -> subprocess.Popen | None:
    """Return the most recently registered child for legacy callers."""
    with _lock:
        active = tuple(_procs.get(job_id, {}).values())
        return active[-1] if active else None


def all_procs() -> list[subprocess.Popen]:
    with _lock:
        return [proc for active in _procs.values() for proc in active.values()]
