from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.config import (
    PIPELINE_CONCURRENCY,
    STEM_PREPROCESS_TARGET_I,
    STEM_PREPROCESS_TRUE_PEAK,
    TIMEOUT_FFMPEG,
    demucs_settings_for_preset,
    ffmpeg_executable,
)
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
_pipeline_lock = asyncio.Semaphore(PIPELINE_CONCURRENCY)


def _pipeline_lock_file() -> Path:
    raw = os.environ.get("STEMDECK_PIPELINE_LOCK", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(tempfile.gettempdir()) / "stemdeck-pipeline.lock"


@contextlib.contextmanager
def _machine_pipeline_lock(job: Job):
    path = _pipeline_lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        _wait_for_machine_lock(job, lock_file)
        try:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(f"{os.getpid()} {job.id}\n")
            lock_file.flush()
            yield
        finally:
            _release_machine_lock(lock_file)


def _wait_for_machine_lock(job: Job, lock_file) -> None:
    waited = False
    while True:
        _check_cancel(job)
        if _try_machine_lock(lock_file):
            if waited:
                logger.info("job %s acquired machine pipeline lock", job.id)
            return
        waited = True
        if job.status == "queued":
            _set(job, status="queued", stage="Waiting for local processing slot...")
        time.sleep(1.0)


def _try_machine_lock(lock_file) -> bool:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
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
    """Transcode any local upload to a 44.1 kHz stereo WAV before
    handing it to Demucs. Normalises MP3 and non-standard WAV formats
    (24-bit, 32-bit float, high sample rate, multi-channel) that Demucs
    would otherwise process silently and output as silence. High-quality
    presets keep this normalization in 32-bit float so hot sources are not
    truncated before the dedicated safety preprocessing pass.

    Deletes the original source file after a successful transcode."""
    dest = job_dir / "source.wav"
    if source.resolve() == dest.resolve():
        return source

    set_stage_progress(job, "acquire", 0.0, status="processing", stage="Preparing audio...")
    settings = demucs_settings_for_preset(job.quality_preset)
    sample_fmt = "flt" if settings.float32 else "s16"
    codec = "pcm_f32le" if settings.float32 else "pcm_s16le"
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
        "-sample_fmt",
        sample_fmt,
        "-c:a",
        codec,
        "-y",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_FFMPEG)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg transcode failed: " + result.stderr.decode("utf-8", errors="replace").strip()
        )
    source.unlink(missing_ok=True)
    return dest


def _prepare_demucs_source(job: Job, source: Path, job_dir: Path) -> Path:
    """Optionally create a safer high-quality working copy for Demucs.

    Hot masters can provoke clipped or ragged stem edges. Feeding Demucs a
    true-peak limited, DC-filtered, slightly quieter float WAV costs extra
    ffmpeg time but preserves the source file and keeps the tweak reversible.
    """
    settings = demucs_settings_for_preset(job.quality_preset)
    loudness_gain = 0.0
    if job.lufs is not None and job.lufs > STEM_PREPROCESS_TARGET_I:
        loudness_gain = STEM_PREPROCESS_TARGET_I - job.lufs
    peak_gain = 0.0
    if job.peak_db is not None and job.peak_db > STEM_PREPROCESS_TRUE_PEAK:
        peak_gain = STEM_PREPROCESS_TRUE_PEAK - job.peak_db
    demucs_gain_db = min(settings.pre_gain_db, loudness_gain, peak_gain, 0.0)
    job.demucs_gain_db = demucs_gain_db
    if abs(demucs_gain_db) < 0.001 and not settings.float32:
        return source

    dest = job_dir / "source.demucs.wav"
    set_stage_progress(
        job,
        "prepare_separation",
        0.25,
        status="processing",
        stage="Preparing high-quality separation...",
    )
    filters = [
        "aresample=44100",
        "aformat=sample_fmts=flt:channel_layouts=stereo",
        # A very low high-pass removes DC/near-DC offset without touching bass fundamentals.
        "highpass=f=12",
    ]
    if abs(demucs_gain_db) >= 0.001:
        filters.append(f"volume={demucs_gain_db:g}dB")
    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter:a",
        ",".join(filters),
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        "-y",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_FFMPEG)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg pre-gain failed: " + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return dest


def _run_common(job: Job, source: Path, job_dir: Path) -> None:
    """Analyze → separate → collect → mix. Shared by both YouTube and local
    upload pipelines after their respective source acquisition steps."""
    _check_cancel(job)
    analyze(job, source)
    _check_cancel(job)
    set_stage_progress(
        job,
        "prepare_separation",
        0.0,
        status="processing",
        stage="Preparing separation input...",
    )
    demucs_source = _prepare_demucs_source(job, source, job_dir)
    set_stage_progress(job, "prepare_separation", 1.0, stage="Separation input ready")
    _check_cancel(job)
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
    job.stem_presence = compute_stem_presence(stems_dir, found)
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
    compute_stem_peaks(stems_dir, all_stem_names)
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
        "chord_progression": job.chord_progression,
        "chord_midi_url": job.chord_midi_url,
        "stem_presence": job.stem_presence,
        "selected_stems": job.selected_stems,
        "quality_preset": job.quality_preset,
        "stem_denoise_preset": job.stem_denoise_preset,
        "demucs_device": job.demucs_device,
        "demucs_device_resolved": job.demucs_device_resolved,
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
    }
    try:
        (job_dir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
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
        async with _pipeline_lock:
            await asyncio.to_thread(_run_with_machine_lock, job, blocking_fn, *fn_args, job_dir)
    except Exception as e:
        if not isinstance(e, JobCancelled) and not job.cancel_requested:
            logger.exception("pipeline failed for job %s: %s", job.id, e)
            _set(job, status="error", stage="Error: Processing failed", error=error_msg)
            persist_registry(jobs_dir)
            _rmtree(job_dir)
            return
        logger.info(
            "pipeline cancelled%s for job %s",
            " (wrapped)" if not isinstance(e, JobCancelled) else "",
            job.id,
        )
        _set(job, status="cancelled", stage="Cancelled")
        persist_registry(jobs_dir)
        _rmtree(job_dir)
        return
    _set(job, status="done", progress=1.0, stage="Done")
    _write_metadata(job, job_dir)
    persist_registry(jobs_dir)


def _run_with_machine_lock(job: Job, blocking_fn, *fn_args: object) -> None:
    *args, job_dir = fn_args
    with _machine_pipeline_lock(job):
        blocking_fn(job, *args, job_dir)


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
