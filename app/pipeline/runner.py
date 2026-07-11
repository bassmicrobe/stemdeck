from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from app.core.config import PIPELINE_CONCURRENCY, TIMEOUT_FFMPEG, ffmpeg_executable
from app.core.files import atomic_write_text
from app.core.joblog import add_job_log
from app.core.models import Job, JobCancelled, _set
from app.core.registry import persist as persist_registry
from app.pipeline.analyze import analyze, compute_stem_presence
from app.pipeline.chords import generate_chord_midi
from app.pipeline.collect import (
    cleanup_source,
    collect,
    compute_stem_peaks,
    denoise_stem_outputs,
    gate_stem_outputs,
    make_original_track,
    make_selected_mix,
    repair_bass_dropouts,
    repair_phase_coherence,
    restore_demucs_gain,
    stabilize_stem_outputs,
)
from app.pipeline.download import download
from app.pipeline.process import run_tracked_process
from app.pipeline.progress import set_stage_progress
from app.pipeline.separate import separate

logger = logging.getLogger("stemdeck.pipeline")


def _rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("failed to remove %s", path, exc_info=True)


# Limit heavy pipeline parallelism by detected local capacity inside one
# backend process. A second file lock below also coordinates multiple local
# LayerLab backends, for example dev server + packaged desktop app.
_separation_lock = threading.BoundedSemaphore(PIPELINE_CONCURRENCY)


def _pipeline_lock_files() -> tuple[Path, ...]:
    raw = os.environ.get("STEMDECK_PIPELINE_LOCK", "").strip()
    base = (
        Path(raw).expanduser().resolve()
        if raw
        else Path(tempfile.gettempdir()) / "stemdeck-pipeline.lock"
    )
    if PIPELINE_CONCURRENCY <= 1:
        return (base,)
    return tuple(base.with_name(f"{base.name}.{slot}") for slot in range(PIPELINE_CONCURRENCY))


@contextlib.contextmanager
def _machine_pipeline_lock(job: Job):
    paths = _pipeline_lock_files()
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    lock_files = [path.open("a+", encoding="utf-8") for path in paths]
    try:
        lock_file, slot = _wait_for_machine_lock(job, lock_files)
        try:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(f"{os.getpid()} {job.id} slot={slot}\n")
            lock_file.flush()
            add_job_log(job, f"Local processing slot {slot + 1}/{len(lock_files)} acquired")
            yield
        finally:
            _release_machine_lock(lock_file)
    finally:
        for handle in lock_files:
            handle.close()


def _wait_for_machine_lock(job: Job, lock_files) -> tuple[object, int]:
    waited = False
    while True:
        _check_cancel(job)
        for slot, lock_file in enumerate(lock_files):
            if _try_machine_lock(lock_file):
                if waited:
                    logger.info("job %s acquired machine pipeline lock slot %s", job.id, slot)
                return lock_file, slot
        waited = True
        if job.status == "queued":
            _set(job, status="queued", stage="Waiting for local processing slot...")
            add_job_log(job, "Waiting for an available local processing slot", stage="queued")
        time.sleep(1.0)


def _try_machine_lock(lock_file) -> bool:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write(" ")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release_machine_lock(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            logger.warning("failed to release machine pipeline lock", exc_info=True)
        return

    import fcntl

    fcntl.flock(lock_file, fcntl.LOCK_UN)


def _check_cancel(job: Job) -> None:
    if job.cancel_requested:
        raise JobCancelled()


def _prepare_local_source(job: Job, source: Path, job_dir: Path) -> Path:
    """Validate an upload and let Demucs' FFmpeg reader decode it once.

    Demucs already resamples and converts channel layouts internally. Eagerly
    creating another WAV doubled decode I/O and could add hundreds of MB of
    scratch data without changing model input.
    """
    del job_dir
    if not source.is_file():
        raise RuntimeError("uploaded source file is missing")
    set_stage_progress(job, "acquire", 1.0, status="processing", stage="Audio ready")
    return source


def _prepare_demucs_source(job: Job, source: Path, job_dir: Path) -> Path:
    """Create one compatibility WAV only when Demucs cannot use its FFmpeg path.

    Demucs normalizes mean and standard deviation itself, so this pass never
    applies gain or limiting. Packaged builds with both ``ffmpeg`` and
    ``ffprobe`` decode the original container directly; imageio-only local
    environments get a single float WAV shared by analysis and separation.
    """
    job.demucs_gain_db = 0.0
    direct_audio = source.suffix.lower() in {".wav", ".wave", ".flac"}
    direct_toolchain = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    if direct_audio or direct_toolchain:
        return source

    dest = job_dir / "source.demucs.wav"
    if dest.is_file() and dest.stat().st_size > 44:
        return dest
    set_stage_progress(
        job,
        "acquire",
        0.9,
        status="processing",
        stage="Preparing decoder-compatible audio...",
    )
    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        "-y",
        str(dest),
    ]
    result = run_tracked_process(job, cmd, timeout=TIMEOUT_FFMPEG)
    if result.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg compatibility transcode failed: {detail}")
    return dest


@contextlib.contextmanager
def _separation_slot(job: Job):
    """Serialize only Demucs while other jobs acquire/analyze/post-process."""
    while not _separation_lock.acquire(timeout=1.0):
        _check_cancel(job)
        _set(job, status="processing", stage="Waiting for separation engine...")
    try:
        with _machine_pipeline_lock(job):
            yield
    finally:
        _separation_lock.release()


def _run_common(job: Job, source: Path, job_dir: Path) -> None:
    """Analyze → separate → collect → mix. Shared by both YouTube and local
    upload pipelines after their respective source acquisition steps."""
    _check_cancel(job)
    demucs_source = _prepare_demucs_source(job, source, job_dir)
    analyze(job, demucs_source)
    _check_cancel(job)
    set_stage_progress(job, "prepare_separation", 1.0, stage="Separation input ready")
    _check_cancel(job)
    with _separation_slot(job):
        stems_root = separate(job, demucs_source, job_dir)
    set_stage_progress(job, "collect", 0.0, status="processing", stage="Collecting stems...")
    found = collect(job, stems_root, job_dir)
    set_stage_progress(job, "collect", 1.0, stage="Stems collected")
    stems_dir = job_dir / "stems"
    set_stage_progress(job, "restore_gain", 0.0, stage="Restoring stem levels...")
    restore_demucs_gain(job, stems_dir, found)
    set_stage_progress(job, "restore_gain", 1.0, stage="Stem levels restored")
    set_stage_progress(job, "bass_repair", 0.0, stage="Checking bass dropouts...")
    job.bass_repair_applied = repair_bass_dropouts(job, source, stems_dir, found)
    set_stage_progress(job, "bass_repair", 1.0, stage="Bass repair complete")
    set_stage_progress(job, "phase_repair", 0.0, stage="Checking phase coherence...")
    repair_phase_coherence(job, source, job_dir, stems_dir, found)
    set_stage_progress(job, "phase_repair", 1.0, stage="Phase repair complete")
    set_stage_progress(job, "denoise", 0.0, stage="Checking stem denoise...")
    job.stem_denoise_applied = denoise_stem_outputs(job, stems_dir, found)
    set_stage_progress(job, "denoise", 1.0, stage="Stem denoise complete")
    set_stage_progress(job, "gate", 0.0, stage="Gating near-silent stem bleed...")
    job.stem_gate_applied = gate_stem_outputs(job, stems_dir, found)
    set_stage_progress(job, "gate", 1.0, stage="Stem gate complete")
    set_stage_progress(job, "stabilize", 0.0, stage="Stabilizing stems...")
    stabilize_stem_outputs(job, stems_dir, found)
    set_stage_progress(job, "stabilize", 1.0, stage="Stems stabilized")
    _check_cancel(job)
    set_stage_progress(job, "presence", 0.0, stage="Measuring stem presence...")
    job.stem_presence = compute_stem_presence(stems_dir, found, job=job)
    set_stage_progress(job, "presence", 1.0, stage="Stem presence measured")
    set_stage_progress(job, "chords", 0.0, stage="Estimating chord MIDI...")
    generate_chord_midi(job, source, job_dir, stems_dir=stems_dir)
    set_stage_progress(job, "chords", 1.0, stage="Chord MIDI ready")
    # Source (100-300 MB or the local upload) is no longer needed after
    # collect; delete it before the ffmpeg amix steps in case scratch space
    # is tight.
    cleanup_source(job_dir)
    job.stems = [{"name": name, "url": f"/api/jobs/{job.id}/stems/{name}.wav"} for name in found]
    _check_cancel(job)
    set_stage_progress(job, "mix", 0.0, stage="Mixing tracks...")
    original_path = make_original_track(job, job_dir, stems_dir)
    set_stage_progress(job, "mix", 0.45, stage="Mixing tracks...")
    if original_path is not None:
        job.stems.insert(
            0,
            {
                "name": "original",
                "url": f"/api/jobs/{job.id}/stems/original.wav",
            },
        )
    _check_cancel(job)
    mix_path = make_selected_mix(job, stems_dir, found)
    set_stage_progress(job, "mix", 1.0, stage="Mixing complete")
    if mix_path is not None:
        job.mix_url = f"/api/jobs/{job.id}/stems/{mix_path.name}"
    _check_cancel(job)

    all_stem_names = [s["name"] for s in job.stems]
    if mix_path is not None and mix_path.stem not in all_stem_names:
        all_stem_names.append(mix_path.stem)
    set_stage_progress(job, "peaks", 0.0, stage="Rendering waveforms...")
    compute_stem_peaks(stems_dir, all_stem_names, job=job)
    set_stage_progress(job, "peaks", 1.0, stage="Waveforms ready")


def _run_blocking(job: Job, url: str, job_dir: Path) -> None:
    _check_cancel(job)
    source = download(job, url, job_dir)
    _run_common(job, source, job_dir)


def _run_local_blocking(job: Job, source_path: Path, job_dir: Path) -> None:
    _check_cancel(job)
    source = _prepare_local_source(job, source_path, job_dir)
    _run_common(job, source, job_dir)


def _write_metadata(job: Job, job_dir: Path) -> None:
    meta = {
        "title": job.title,
        "thumbnail": job.thumbnail,
        "duration_sec": job.duration_sec,
        "bpm": job.bpm,
        "key": job.key,
        "scale": job.scale,
        "key_confidence": job.key_confidence,
        "lufs": job.lufs,
        "peak_db": job.peak_db,
        "dynamic_range": job.dynamic_range,
        "tempo_stability": job.tempo_stability,
        "beat_times": job.beat_times,
        "downbeat_times": job.downbeat_times,
        "beat_tracker": job.beat_tracker,
        "chord_progression": job.chord_progression,
        "chord_midi_url": job.chord_midi_url,
        "midi_analysis": job.midi_analysis,
        "midi_analysis_url": job.midi_analysis_url,
        "stem_presence": job.stem_presence,
        "selected_stems": job.selected_stems,
        "quality_preset": job.quality_preset,
        "stem_denoise_preset": job.stem_denoise_preset,
        "demucs_device": job.demucs_device,
        "demucs_device_resolved": job.demucs_device_resolved,
        "demucs_engine": job.demucs_engine,
        "pcm_engine": job.pcm_engine,
        "profile_key": job.profile_key(),
        "profile_label": job.profile_label(),
        "source_url": job.source_url,
        "demucs_gain_db": job.demucs_gain_db,
        "bass_repair_applied": job.bass_repair_applied,
        "phase_repair_applied": job.phase_repair_applied,
        "phase_repair_residual_ratio": job.phase_repair_residual_ratio,
        "stem_denoise_applied": job.stem_denoise_applied,
        "stem_gate_applied": job.stem_gate_applied,
        "stem_gate_threshold_db": job.stem_gate_threshold_db,
        "processing_started_at": job.processing_started_at,
        "completed_at": job.completed_at,
        "processing_elapsed_seconds": job.processing_elapsed_seconds,
        "tags": job.tags,
        "logs": job.logs,
    }
    try:
        atomic_write_text(job_dir / "metadata.json", json.dumps(meta, indent=2) + "\n")
    except OSError:
        logger.warning("could not write metadata.json for job %s", job.id, exc_info=True)


async def _run_async(
    job: Job,
    job_dir: Path,
    jobs_dir: Path,
    blocking_fn,
    *fn_args: object,
    error_msg: str = "Audio processing failed. Please try again.",
) -> None:
    """Common async wrapper: acquires the pipeline lock, runs blocking_fn in a
    thread, then handles success / cancel / error outcomes uniformly."""
    try:
        add_job_log(job, "Job entered the processing queue", stage="queued", progress=job.progress)
        await asyncio.to_thread(blocking_fn, job, *fn_args, job_dir)
    except Exception as e:
        if not isinstance(e, JobCancelled) and not job.cancel_requested:
            logger.exception("pipeline failed for job %s: %s", job.id, e)
            add_job_log(job, e, level="error", stage="error", progress=job.progress)
            _set(job, status="error", stage="Error: Processing failed", error=error_msg)
            persist_registry(jobs_dir)
            _rmtree(job_dir)
            return
        logger.info(
            "pipeline cancelled%s for job %s",
            " (wrapped)" if not isinstance(e, JobCancelled) else "",
            job.id,
        )
        add_job_log(job, "Job cancelled", level="warning", stage="cancelled", progress=job.progress)
        _set(job, status="cancelled", stage="Cancelled")
        persist_registry(jobs_dir)
        _rmtree(job_dir)
        return
    _set(job, status="done", progress=1.0, stage="Done")
    add_job_log(job, "Processing completed successfully", stage="done", progress=1.0)
    _write_metadata(job, job_dir)
    persist_registry(jobs_dir)


async def run_pipeline(job: Job, url: str, jobs_dir: Path) -> None:
    job_dir = jobs_dir / job.id
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.exception("pipeline failed for job %s: %s", job.id, e)
        _set(
            job,
            status="error",
            stage="Error: Processing failed",
            error="Audio processing failed. Please try another video.",
        )
        persist_registry(jobs_dir)
        return
    await _run_async(
        job,
        job_dir,
        jobs_dir,
        _run_blocking,
        url,
        error_msg="Audio processing failed. Please try another video.",
    )


async def run_local_pipeline(job: Job, source_path: Path, jobs_dir: Path) -> None:
    """Run the stem-separation pipeline for a locally uploaded file.
    The job directory and source file are already present on disk (created
    by the API handler before this task is scheduled)."""
    job_dir = jobs_dir / job.id
    await _run_async(job, job_dir, jobs_dir, _run_local_blocking, source_path)
