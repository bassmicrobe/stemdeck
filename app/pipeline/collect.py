from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.core.config import (
    BASS_REPAIR_LOW_PASS_HZ,
    BASS_REPAIR_MAX_BLEND,
    BASS_REPAIR_SHORT_GAP_MS,
    BASS_REPAIR_SHORT_GAP_RATIO,
    BASS_REPAIR_TRIGGER_RATIO,
    JOB_TTL_SECONDS,
    PHASE_REPAIR_FLOOR_DB,
    PHASE_REPAIR_MAX_BLEND,
    STEM_GATE_ATTACK_MS,
    STEM_GATE_HOLD_MS,
    STEM_GATE_RELEASE_MS,
    STEM_GATE_THRESHOLD_DB,
    STEM_GATE_WINDOW_MS,
    STEM_NAMES,
    STEM_POST_LIMITER_PEAK,
    TIMEOUT_FFMPEG,
    bass_repair_enabled_for_preset,
    demucs_settings_for_preset,
    ffmpeg_executable,
    normalize_stem_denoise_preset,
    output_limiter_filter,
    phase_repair_enabled_for_preset,
    phase_repair_max_blend_for_preset,
    stem_denoise_filter_for_preset,
    stem_gate_enabled_for_preset,
    wav_codec_for_quality_preset,
)
from app.core.models import Job, _set
from app.core.registry import add_proc, remove_proc
from app.core.registry import all_jobs as registry_all
from app.core.registry import persist as registry_persist
from app.core.registry import remove as registry_remove
from app.pipeline.process import popen_background, terminate_process
from app.pipeline.progress import set_stage_progress

logger = logging.getLogger("stemdeck.collect")


@dataclass(frozen=True)
class PhaseRepairResult:
    changed: bool
    residual_ratio: float | None = None


def _rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("failed to remove %s", path, exc_info=True)


def _run_ffmpeg(job: Job, cmd: list[str]) -> bool:
    """Run an ffmpeg command, registering the subprocess with the job
    registry so POST /api/jobs/{id}/cancel can terminate it. Returns
    True on success, False on failure or external termination.

    Without registering the proc, an in-flight ffmpeg amix would block
    cancellation for up to its 300s timeout -- the cancel flag is set
    but the runner can't see it until subprocess.run returns. With
    process registration, the cancel API can call proc.terminate() directly and
    communicate() returns within ~1s with a non-zero returncode."""
    proc = popen_background(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    add_proc(job.id, proc)
    try:
        try:
            _, stderr = proc.communicate(timeout=TIMEOUT_FFMPEG)
        except subprocess.TimeoutExpired:
            terminate_process(proc, force=True)
            proc.communicate()
            logger.warning("ffmpeg timed out for job %s", job.id)
            return False
        if proc.returncode != 0:
            tail = (stderr or b"").decode(errors="replace").splitlines()[-3:]
            logger.warning(
                "ffmpeg exit %s for job %s: %s",
                proc.returncode,
                job.id,
                " | ".join(tail) or "(no stderr)",
            )
            return False
        return True
    finally:
        remove_proc(job.id, proc)


_TERMINAL = frozenset(("done", "error", "cancelled"))


def collect(job: Job, stems_root: Path, job_dir: Path) -> list[str]:
    """Move Demucs-emitted stems into the job's stems/ dir and clean up
    the demucs intermediate dir. Does NOT delete the source download --
    cleanup_source() is called by the runner after any post-processing
    that needs to re-encode the source (e.g. building original.wav)."""
    target_dir = job_dir / "stems"
    target_dir.mkdir(exist_ok=True)
    found: list[str] = []
    existing = [name for name in STEM_NAMES if (stems_root / f"{name}.wav").exists()]
    total = max(1, len(existing))
    for idx, name in enumerate(existing):
        src = stems_root / f"{name}.wav"
        set_stage_progress(
            job,
            "collect",
            idx / total,
            stage=f"Collecting stem {idx + 1}/{total}: {name}",
        )
        shutil.move(str(src), target_dir / f"{name}.wav")
        found.append(name)
        set_stage_progress(
            job,
            "collect",
            (idx + 1) / (total + 1),
            stage=f"Collected stem {idx + 1}/{total}: {name}",
        )
    set_stage_progress(job, "collect", 0.92, stage="Cleaning separation workspace...")
    _rmtree(job_dir / demucs_settings_for_preset(job.quality_preset).model)
    if not found:
        raise RuntimeError("no stems produced by demucs")
    return found


def restore_demucs_gain(job: Job, stems_dir: Path, stem_names: list[str]) -> None:
    """Undo any pre-Demucs gain applied to the working copy.

    Quality presets may feed Demucs a quieter file to reduce clipping-like
    artifacts on hot masters. The model output follows that lower level, so
    restore the inverse gain before downstream waveform, mix, and download
    artifacts are generated.
    """
    settings = demucs_settings_for_preset(job.quality_preset)
    applied_gain_db = (
        float(job.demucs_gain_db) if job.demucs_gain_db is not None else settings.pre_gain_db
    )
    if abs(applied_gain_db) < 0.001:
        return

    restore_db = -applied_gain_db
    old_stage = job.stage_message
    _set(job, stage="Restoring stem levels...")
    available = [name for name in stem_names if (stems_dir / f"{name}.wav").is_file()]
    total = max(1, len(available))
    for idx, name in enumerate(available):
        path = stems_dir / f"{name}.wav"
        set_stage_progress(
            job,
            "restore_gain",
            idx / total,
            stage=f"Restoring stem levels {idx + 1}/{total}: {name}",
        )
        tmp = path.with_suffix(".gain.wav")
        cmd = [
            ffmpeg_executable(),
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-filter:a",
            f"volume={restore_db:g}dB",
            "-c:a",
            "pcm_f32le",
            str(tmp),
        ]
        if not _run_ffmpeg(job, cmd):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg gain restore failed for {name}")
        tmp.replace(path)
        set_stage_progress(
            job,
            "restore_gain",
            (idx + 1) / total,
            stage=f"Restored stem levels {idx + 1}/{total}: {name}",
        )
    _set(job, stage=old_stage)


def _write_bass_residual_candidate(
    job: Job, source: Path, stems_dir: Path, stem_names: list[str], out: Path
) -> bool:
    """Render a low-passed residual candidate: source minus non-bass stems."""
    non_bass = [
        name for name in stem_names if name != "bass" and (stems_dir / f"{name}.wav").is_file()
    ]
    if not non_bass:
        return False

    cmd: list[str] = [
        ffmpeg_executable(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
    ]
    for name in non_bass:
        cmd += ["-i", str(stems_dir / f"{name}.wav")]

    filter_parts = [f"[{idx}:a]volume=-1.0[neg{idx}]" for idx in range(1, len(non_bass) + 1)]
    mix_inputs = "[0:a]" + "".join(f"[neg{idx}]" for idx in range(1, len(non_bass) + 1))
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(non_bass) + 1}:normalize=0,"
        f"lowpass=f={BASS_REPAIR_LOW_PASS_HZ:g}[repair]"
    )
    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[repair]",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(out),
    ]
    return _run_ffmpeg(job, cmd)


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    if values.size == 0:
        return values
    window = max(1, min(window, values.size))
    left = window // 2
    right = window - 1 - left
    padded = np.pad(values, (left, right), mode="edge")
    cumsum = np.cumsum(np.insert(padded, 0, 0.0, axis=0), dtype=np.float64)
    return ((cumsum[window:] - cumsum[:-window]) / window).astype(np.float32)


def _rms_envelope(block: np.ndarray, window: int) -> np.ndarray:
    mono_power = np.mean(block * block, axis=1, dtype=np.float32)
    return np.sqrt(_moving_average(mono_power, window) + 1e-12)


def _boolean_moving_fill(mask: np.ndarray, window: int) -> np.ndarray:
    if mask.size == 0:
        return mask
    smoothed = _moving_average(mask.astype(np.float32), window)
    return smoothed > 0.08


def _match_channels(block: np.ndarray, channels: int) -> np.ndarray:
    if block.shape[1] == channels:
        return block
    if block.shape[1] == 1:
        return np.repeat(block, channels, axis=1)
    return block[:, :channels]


def _blend_bass_dropout_repair(
    bass_path: Path,
    residual_path: Path,
    out_path: Path,
    *,
    max_blend: float = BASS_REPAIR_MAX_BLEND,
    trigger_ratio: float = BASS_REPAIR_TRIGGER_RATIO,
    subtype: str = "FLOAT",
    progress_callback: Callable[[float], None] | None = None,
) -> bool:
    """Blend low-passed residual into bass only where the bass stem drops out.

    This is deliberately conservative: the residual candidate is derived from
    the original mix, then low-passed by ffmpeg. A smoothed energy comparison
    gates the blend so normal bass passages are left untouched.
    """
    changed = False
    blocksize = 262_144
    floor = 10 ** (-54 / 20)

    with (
        sf.SoundFile(bass_path) as bass_file,
        sf.SoundFile(residual_path) as residual_file,
    ):
        if bass_file.samplerate != residual_file.samplerate:
            logger.warning(
                "skip bass repair for %s: sample-rate mismatch %s != %s",
                bass_path,
                bass_file.samplerate,
                residual_file.samplerate,
            )
            return False

        channels = bass_file.channels
        total_frames = max(1, bass_file.frames)
        processed_frames = 0
        window = max(128, int(bass_file.samplerate * 0.028))
        smooth = max(64, int(bass_file.samplerate * 0.012))
        short_gap = max(16, int(bass_file.samplerate * (BASS_REPAIR_SHORT_GAP_MS / 1000)))

        with sf.SoundFile(
            out_path,
            mode="w",
            samplerate=bass_file.samplerate,
            channels=channels,
            subtype=subtype,
        ) as out_file:
            while True:
                bass_block = bass_file.read(blocksize, dtype="float32", always_2d=True)
                if bass_block.size == 0:
                    break
                residual_block = residual_file.read(
                    len(bass_block), dtype="float32", always_2d=True
                )
                if len(residual_block) < len(bass_block):
                    pad = np.zeros(
                        (len(bass_block) - len(residual_block), residual_block.shape[1]),
                        dtype=np.float32,
                    )
                    residual_block = np.vstack((residual_block, pad))
                residual_block = _match_channels(residual_block, channels)

                bass_env = _rms_envelope(bass_block, window)
                residual_env = _rms_envelope(residual_block, window)
                deficit = residual_env - (bass_env * trigger_ratio)
                weight = np.clip(deficit / (residual_env + 1e-8), 0.0, max_blend)
                short_gap_mask = _boolean_moving_fill(
                    (residual_env > floor) & (bass_env * BASS_REPAIR_SHORT_GAP_RATIO < residual_env),
                    short_gap,
                )
                short_gap_weight = np.where(short_gap_mask, max_blend * 0.72, 0.0)
                weight = np.maximum(weight, short_gap_weight)
                weight = np.where(residual_env > floor, weight, 0.0).astype(np.float32)
                weight = _moving_average(weight, smooth)

                if float(np.max(weight, initial=0.0)) > 0.001:
                    changed = True
                repaired = bass_block + (residual_block * weight[:, None])
                repaired = _soft_limit_block(repaired)
                out_file.write(repaired)
                processed_frames += len(bass_block)
                if progress_callback is not None:
                    progress_callback(min(1.0, processed_frames / total_frames))

    return changed


def _soft_limit_block(block: np.ndarray, peak: float = STEM_POST_LIMITER_PEAK) -> np.ndarray:
    """Clamp only unsafe overs after repair/mix math while preserving float32 output."""
    if block.size == 0:
        return block
    current = float(np.max(np.abs(block), initial=0.0))
    if current <= peak:
        return block.astype(np.float32, copy=False)
    return np.clip(block * (peak / current), -peak, peak).astype(np.float32, copy=False)


def stabilize_stem_outputs(job: Job, stems_dir: Path, stem_names: list[str]) -> None:
    """Remove DC offset and prevent accidental clipping in generated stems.

    Demucs + residual repair are intentionally float-heavy. This pass keeps the
    files as float for high-quality presets, but subtracts tiny DC offsets and
    rescales only files that exceed the configured safety peak.
    """
    if not demucs_settings_for_preset(job.quality_preset).float32:
        return
    old_stage = job.stage_message
    _set(job, stage="Stabilizing stems...")
    try:
        for name in stem_names:
            path = stems_dir / f"{name}.wav"
            if not path.is_file():
                continue
            tmp = path.with_suffix(".stable.wav")
            should_replace = False
            with sf.SoundFile(path) as src:
                subtype = "FLOAT"
                channels = src.channels
                samplerate = src.samplerate
                blocksize = 262_144
                sums = np.zeros(channels, dtype=np.float64)
                frames = 0
                peak = 0.0
                while True:
                    block = src.read(blocksize, dtype="float32", always_2d=True)
                    if block.size == 0:
                        break
                    sums += np.sum(block, axis=0, dtype=np.float64)
                    frames += len(block)
                    peak = max(peak, float(np.max(np.abs(block), initial=0.0)))
                if frames == 0:
                    continue
                dc = (sums / frames).astype(np.float32)
                needs_dc = float(np.max(np.abs(dc), initial=0.0)) > 1e-5
                gain = min(1.0, STEM_POST_LIMITER_PEAK / peak) if peak > 0 else 1.0
                needs_gain = gain < 0.9999
                if not needs_dc and not needs_gain:
                    continue

                src.seek(0)
                with sf.SoundFile(
                    tmp,
                    mode="w",
                    samplerate=samplerate,
                    channels=channels,
                    subtype=subtype,
                ) as out:
                    while True:
                        block = src.read(blocksize, dtype="float32", always_2d=True)
                        if block.size == 0:
                            break
                        if needs_dc:
                            block = block - dc[None, :]
                        if needs_gain:
                            block = block * gain
                        out.write(block.astype(np.float32, copy=False))
                should_replace = True
            if should_replace:
                tmp.replace(path)
                logger.info("stabilized stem %s for job %s", name, job.id)
    finally:
        for tmp in stems_dir.glob("*.stable.wav"):
            tmp.unlink(missing_ok=True)
        _set(job, stage=old_stage)


def repair_bass_dropouts(job: Job, source: Path, stems_dir: Path, stem_names: list[str]) -> bool:
    """Repair short bass dropouts using a low-frequency residual candidate.

    Demucs can occasionally under-estimate bass on dense, hot masters. For
    high-quality jobs, derive a low-passed residual from the original mix minus
    the other stems and blend it only into sections where bass energy is
    suspiciously absent.
    """
    if not bass_repair_enabled_for_preset(job.quality_preset):
        return False
    if "bass" not in stem_names or not (stems_dir / "bass.wav").is_file():
        return False

    old_stage = job.stage_message
    residual_path = stems_dir / "bass.residual.wav"
    repaired_path = stems_dir / "bass.repaired.wav"
    _set(job, stage="Repairing bass dropouts...")
    try:
        if not _write_bass_residual_candidate(job, source, stems_dir, stem_names, residual_path):
            return False
        set_stage_progress(job, "bass_repair", 0.35, stage="Blending bass repair...")
        subtype = (
            "FLOAT" if wav_codec_for_quality_preset(job.quality_preset) == "pcm_f32le" else "PCM_16"
        )
        if not _blend_bass_dropout_repair(
            stems_dir / "bass.wav",
            residual_path,
            repaired_path,
            subtype=subtype,
            progress_callback=lambda fraction: set_stage_progress(
                job,
                "bass_repair",
                0.35 + (0.6 * fraction),
                stage=f"Blending bass repair {round(fraction * 100)}%",
            ),
        ):
            return False
        repaired_path.replace(stems_dir / "bass.wav")
        logger.info("bass dropout repair applied for job %s", job.id)
        return True
    except Exception:
        logger.warning("bass dropout repair skipped for job %s", job.id, exc_info=True)
        return False
    finally:
        residual_path.unlink(missing_ok=True)
        repaired_path.unlink(missing_ok=True)
        _set(job, stage=old_stage)


def _write_phase_reference(job: Job, source: Path, out: Path) -> bool:
    """Render the original source into the same float/stereo space as stems."""
    cmd = [
        ffmpeg_executable(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter:a",
        "aresample=44100,aformat=sample_fmts=flt:channel_layouts=stereo,highpass=f=12",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(out),
    ]
    return _run_ffmpeg(job, cmd)


def _pad_block(block: np.ndarray, frames: int) -> np.ndarray:
    if len(block) >= frames:
        return block
    channels = block.shape[1] if block.ndim == 2 and block.shape[1] > 0 else 1
    pad = np.zeros((frames - len(block), channels), dtype=np.float32)
    return np.vstack((block, pad))


def _blend_phase_residual(
    reference_path: Path,
    stems_dir: Path,
    stem_names: list[str],
    out_paths: list[Path],
    *,
    max_blend: float = PHASE_REPAIR_MAX_BLEND,
    floor_db: float = PHASE_REPAIR_FLOOR_DB,
    subtype: str = "FLOAT",
    progress_callback: Callable[[float], None] | None = None,
) -> PhaseRepairResult:
    """Distribute source-minus-stem-sum residual back into active stems.

    The correction is energy-weighted instead of copied into every stem. That
    keeps the summed playback closer to the source while limiting bleed in
    isolated stems.
    """
    if max_blend <= 0 or len(stem_names) != len(out_paths):
        return PhaseRepairResult(False)

    changed = False
    before_power = 0.0
    after_power = 0.0
    blocksize = 262_144
    floor = 10 ** (floor_db / 20)

    with contextlib.ExitStack() as stack:
        reference_file = stack.enter_context(sf.SoundFile(reference_path))
        samplerate = reference_file.samplerate
        channels = reference_file.channels
        total_frames = max(1, reference_file.frames)
        processed_frames = 0

        stem_files: list[sf.SoundFile] = []
        for name in stem_names:
            path = stems_dir / f"{name}.wav"
            stem_file = stack.enter_context(sf.SoundFile(path))
            if stem_file.samplerate != samplerate:
                logger.warning(
                    "skip phase repair: sample-rate mismatch for %s (%s != %s)",
                    path,
                    stem_file.samplerate,
                    samplerate,
                )
                return PhaseRepairResult(False)
            stem_files.append(stem_file)

        out_files = [
            stack.enter_context(
                sf.SoundFile(
                    out,
                    mode="w",
                    samplerate=samplerate,
                    channels=channels,
                    subtype=subtype,
                )
            )
            for out in out_paths
        ]

        window = max(128, int(samplerate * 0.026))
        smooth = max(64, int(samplerate * 0.010))

        while True:
            reference = reference_file.read(blocksize, dtype="float32", always_2d=True)
            if reference.size == 0:
                break
            reference = _match_channels(reference, channels)
            frames = len(reference)

            stem_blocks = []
            envelopes = []
            for stem_file in stem_files:
                block = stem_file.read(frames, dtype="float32", always_2d=True)
                block = _match_channels(_pad_block(block, frames), channels)
                stem_blocks.append(block)
                envelopes.append(_rms_envelope(block, window))

            stem_sum = np.sum(np.stack(stem_blocks, axis=0), axis=0, dtype=np.float32)
            residual = reference - stem_sum
            before_power += float(np.sum(residual * residual, dtype=np.float64))
            residual_env = _rms_envelope(residual, window)
            env_matrix = np.stack(envelopes, axis=1)
            env_sum = np.sum(env_matrix, axis=1, dtype=np.float32)
            active = (residual_env > floor) & (env_sum > floor)

            repaired_sum = np.zeros_like(reference, dtype=np.float32)
            for idx, block in enumerate(stem_blocks):
                weight = np.where(active, env_matrix[:, idx] / (env_sum + 1e-8), 0.0)
                weight = _moving_average(weight.astype(np.float32), smooth)
                correction = residual * (weight[:, None] * max_blend)
                if float(np.max(np.abs(correction), initial=0.0)) > 1e-5:
                    changed = True
                repaired = _soft_limit_block(block + correction)
                repaired_sum += repaired
                out_files[idx].write(repaired)
            after_residual = reference - repaired_sum
            after_power += float(np.sum(after_residual * after_residual, dtype=np.float64))
            processed_frames += frames
            if progress_callback is not None:
                progress_callback(min(1.0, processed_frames / total_frames))

    ratio = after_power / before_power if before_power > 1e-18 else None
    return PhaseRepairResult(changed, ratio)


def repair_phase_coherence(
    job: Job,
    source: Path,
    job_dir: Path,
    stems_dir: Path,
    stem_names: list[str],
) -> bool:
    """Reduce stem-sum phase/residual mismatch against the original source."""
    if not phase_repair_enabled_for_preset(job.quality_preset):
        return False
    available = [name for name in stem_names if (stems_dir / f"{name}.wav").is_file()]
    if len(available) < 2:
        return False

    old_stage = job.stage_message
    reference_path = job_dir / "source.phase.wav"
    tmp_paths = [stems_dir / f"{name}.phase.wav" for name in available]
    _set(job, stage="Repairing phase coherence...")
    try:
        if not _write_phase_reference(job, source, reference_path):
            return False
        set_stage_progress(job, "phase_repair", 0.25, stage="Blending phase correction...")
        subtype = (
            "FLOAT" if wav_codec_for_quality_preset(job.quality_preset) == "pcm_f32le" else "PCM_16"
        )
        result = _blend_phase_residual(
            reference_path,
            stems_dir,
            available,
            tmp_paths,
            max_blend=phase_repair_max_blend_for_preset(job.quality_preset),
            subtype=subtype,
            progress_callback=lambda fraction: set_stage_progress(
                job,
                "phase_repair",
                0.25 + (0.7 * fraction),
                stage=f"Blending phase correction {round(fraction * 100)}%",
            ),
        )
        job.phase_repair_residual_ratio = result.residual_ratio
        if not result.changed:
            return False
        for name, tmp in zip(available, tmp_paths, strict=False):
            tmp.replace(stems_dir / f"{name}.wav")
        job.phase_repair_applied = True
        logger.info("phase coherence repair applied for job %s", job.id)
        return True
    except Exception:
        logger.warning("phase coherence repair skipped for job %s", job.id, exc_info=True)
        return False
    finally:
        reference_path.unlink(missing_ok=True)
        for tmp in tmp_paths:
            tmp.unlink(missing_ok=True)
        _set(job, stage=old_stage)


def denoise_stem_outputs(job: Job, stems_dir: Path, stem_names: list[str]) -> bool:
    """Optionally denoise each separated stem with ffmpeg's afftdn filter.

    The pass is all-or-nothing: all temporary denoised files must render before
    any original stem is replaced. If ffmpeg cannot denoise a stem, the job
    keeps the original separation and records stem_denoise_applied=False.
    """
    preset = normalize_stem_denoise_preset(job.stem_denoise_preset)
    job.stem_denoise_preset = preset
    filter_expr = stem_denoise_filter_for_preset(preset)
    if not filter_expr:
        return False

    available = [name for name in stem_names if (stems_dir / f"{name}.wav").is_file()]
    if not available:
        return False

    old_stage = job.stage_message
    tmp_pairs: list[tuple[Path, Path]] = []
    wav_codec = wav_codec_for_quality_preset(job.quality_preset)
    _set(job, stage=f"Denoising stems ({preset})...")
    try:
        total = max(1, len(available))
        for idx, name in enumerate(available):
            path = stems_dir / f"{name}.wav"
            tmp = path.with_suffix(".denoise.wav")
            tmp_pairs.append((path, tmp))
            set_stage_progress(
                job,
                "denoise",
                idx / total,
                stage=f"Denoising stem {idx + 1}/{total}: {name}",
            )
            cmd = [
                ffmpeg_executable(),
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-filter:a",
                filter_expr,
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                wav_codec,
                str(tmp),
            ]
            if not _run_ffmpeg(job, cmd):
                return False
            set_stage_progress(
                job,
                "denoise",
                (idx + 1) / total,
                stage=f"Denoised stem {idx + 1}/{total}: {name}",
            )
        for path, tmp in tmp_pairs:
            tmp.replace(path)
        logger.info("stem denoise %s applied for job %s", preset, job.id)
        return True
    except Exception:
        logger.warning("stem denoise skipped for job %s", job.id, exc_info=True)
        return False
    finally:
        for _, tmp in tmp_pairs:
            tmp.unlink(missing_ok=True)
        _set(job, stage=old_stage)


def _iter_true_runs(mask: np.ndarray):
    if mask.size == 0:
        return
    padded = np.concatenate(([False], mask.astype(bool, copy=False), [False]))
    changes = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    yield from zip(starts, ends, strict=False)


def _gate_gain_from_mask(
    active: np.ndarray,
    *,
    window_ms: int = STEM_GATE_WINDOW_MS,
    hold_ms: int = STEM_GATE_HOLD_MS,
    attack_ms: int = STEM_GATE_ATTACK_MS,
    release_ms: int = STEM_GATE_RELEASE_MS,
) -> np.ndarray:
    """Build per-envelope-frame gain so gate edges fade instead of clicking."""
    if active.size == 0:
        return np.array([], dtype=np.float32)
    active = active.astype(bool, copy=False)
    expanded = active.copy()
    hold_frames = max(0, int(np.ceil(hold_ms / max(1, window_ms))))
    if hold_frames:
        for start, end in _iter_true_runs(active):
            expanded[max(0, start - hold_frames) : min(active.size, end + hold_frames)] = True

    gain = np.zeros(active.size, dtype=np.float32)
    attack_frames = max(1, int(np.ceil(attack_ms / max(1, window_ms))))
    release_frames = max(1, int(np.ceil(release_ms / max(1, window_ms))))
    for start, end in _iter_true_runs(expanded):
        gain[start:end] = 1.0
        attack_start = max(0, start - attack_frames)
        if start > attack_start:
            fade = np.linspace(0.0, 1.0, start - attack_start, endpoint=False, dtype=np.float32)
            gain[attack_start:start] = np.maximum(gain[attack_start:start], fade)
        release_end = min(active.size, end + release_frames)
        if release_end > end:
            fade = np.linspace(1.0, 0.0, release_end - end, endpoint=False, dtype=np.float32)
            gain[end:release_end] = np.maximum(gain[end:release_end], fade)
    return gain


def _read_gate_envelope(path: Path, frame_len: int) -> np.ndarray:
    envelopes: list[np.ndarray] = []
    blocksize = max(frame_len, frame_len * 512)
    with sf.SoundFile(path) as src:
        while True:
            block = src.read(blocksize, dtype="float32", always_2d=True)
            if block.size == 0:
                break
            remainder = len(block) % frame_len
            if remainder:
                pad = np.zeros((frame_len - remainder, block.shape[1]), dtype=np.float32)
                block = np.vstack((block, pad))
            frames = block.reshape(-1, frame_len, block.shape[1])
            rms = np.sqrt(np.mean(frames * frames, axis=(1, 2), dtype=np.float64) + 1e-12)
            envelopes.append(rms.astype(np.float32))
    if not envelopes:
        return np.array([], dtype=np.float32)
    return np.concatenate(envelopes)


def _write_gated_stem(
    path: Path,
    out_path: Path,
    *,
    threshold_db: float = STEM_GATE_THRESHOLD_DB,
    subtype: str = "FLOAT",
) -> bool:
    threshold = 10 ** (threshold_db / 20)
    with sf.SoundFile(path) as src:
        samplerate = src.samplerate
        channels = src.channels
        frame_len = max(64, int(samplerate * (STEM_GATE_WINDOW_MS / 1000)))

    envelope = _read_gate_envelope(path, frame_len)
    if envelope.size == 0:
        return False
    active = envelope >= threshold
    gain = _gate_gain_from_mask(active)
    if gain.size == 0 or float(np.min(gain, initial=1.0)) >= 0.999:
        return False

    blocksize = max(frame_len, frame_len * 512)
    cursor = 0
    with (
        sf.SoundFile(path) as src,
        sf.SoundFile(
            out_path,
            mode="w",
            samplerate=samplerate,
            channels=channels,
            subtype=subtype,
        ) as out,
    ):
        while True:
            block = src.read(blocksize, dtype="float32", always_2d=True)
            if block.size == 0:
                break
            start_frame = cursor // frame_len
            end_frame = min(gain.size, (cursor + len(block) + frame_len - 1) // frame_len)
            frame_gain = np.repeat(gain[start_frame:end_frame], frame_len)
            offset = cursor - (start_frame * frame_len)
            sample_gain = frame_gain[offset : offset + len(block)]
            if sample_gain.size < len(block):
                sample_gain = np.pad(
                    sample_gain,
                    (0, len(block) - sample_gain.size),
                    constant_values=float(gain[-1]),
                )
            out.write((block * sample_gain[:, None]).astype(np.float32, copy=False))
            cursor += len(block)
    return True


def gate_stem_outputs(job: Job, stems_dir: Path, stem_names: list[str]) -> bool:
    """Mute near-silent stem bleed without shortening any stem files.

    The gate is deliberately post-separation and timeline-preserving: it zeros
    quiet regions with short fades, rather than removing samples. That keeps all
    stems aligned for playback/export while reducing residual hiss and bleed.
    """
    if not stem_gate_enabled_for_preset(job.quality_preset):
        return False

    available = [name for name in stem_names if (stems_dir / f"{name}.wav").is_file()]
    if not available:
        return False

    old_stage = job.stage_message
    tmp_pairs: list[tuple[Path, Path]] = []
    subtype = "FLOAT" if wav_codec_for_quality_preset(job.quality_preset) == "pcm_f32le" else "PCM_16"
    _set(job, stage="Gating near-silent stem bleed...")
    try:
        total = max(1, len(available))
        for idx, name in enumerate(available):
            path = stems_dir / f"{name}.wav"
            tmp = path.with_suffix(".gate.wav")
            set_stage_progress(
                job,
                "gate",
                idx / total,
                stage=f"Gating stem {idx + 1}/{total}: {name}",
            )
            if _write_gated_stem(path, tmp, threshold_db=STEM_GATE_THRESHOLD_DB, subtype=subtype):
                tmp_pairs.append((path, tmp))
            else:
                tmp.unlink(missing_ok=True)
            set_stage_progress(
                job,
                "gate",
                (idx + 1) / total,
                stage=f"Checked stem gate {idx + 1}/{total}: {name}",
            )
        if not tmp_pairs:
            return False
        for path, tmp in tmp_pairs:
            tmp.replace(path)
        job.stem_gate_threshold_db = STEM_GATE_THRESHOLD_DB
        logger.info("stem gate applied to %s stem(s) for job %s", len(tmp_pairs), job.id)
        return True
    except Exception:
        logger.warning("stem gate skipped for job %s", job.id, exc_info=True)
        return False
    finally:
        for _, tmp in tmp_pairs:
            tmp.unlink(missing_ok=True)
        _set(job, stage=old_stage)


def cleanup_source(job_dir: Path) -> None:
    """Delete the source audio file. Called after collect AND after any
    post-processing that re-encodes the source (make_original_track).
    The source is 100-300 MB, so getting rid of it is the bulk of disk
    reclaim per job; only the stems remain."""
    for f in job_dir.glob("source.*"):
        f.unlink(missing_ok=True)


def make_original_track(job: Job, job_dir: Path, stems_dir: Path) -> Path | None:
    """Build the "Original" backing track at stems/original.wav as the
    sum of the stems the user did NOT select. This way the studio can
    play (original + each selected stem) and reconstruct the full song
    without doubling the selected stems -- which is what would happen
    if "original" were the raw source download (drum hits in original
    + isolated drums.wav = drums at 2x amplitude).

    Skipped when the user kept all 6 stems (no complement to mix) or
    when none of the unselected stem WAVs are on disk."""
    unselected = [s for s in STEM_NAMES if s not in job.selected_stems]
    inputs = [stems_dir / f"{name}.wav" for name in unselected]
    inputs = [p for p in inputs if p.exists()]
    if not inputs:
        return None
    out = stems_dir / "original.wav"
    wav_codec = wav_codec_for_quality_preset(job.quality_preset)
    cmd: list[str] = [
        ffmpeg_executable(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
    ]
    for p in inputs:
        cmd += ["-i", str(p)]
    if len(inputs) == 1:
        # Single complement stem -- copy as-is so we still produce a
        # canonical mix.wav-shaped output without invoking amix on a
        # 1-input graph (which is a no-op anyway).
        cmd += ["-c:a", wav_codec, str(out)]
    else:
        filter_inputs = "".join(f"[{i}:a]" for i in range(len(inputs)))
        cmd += [
            "-filter_complex",
            f"{filter_inputs}amix=inputs={len(inputs)}:normalize=0",
            "-c:a",
            wav_codec,
            str(out),
        ]
    return out if _run_ffmpeg(job, cmd) else None


def make_selected_mix(job: Job, stems_dir: Path, found: list[str]) -> Path | None:
    """If the user picked a strict subset of stems at submit time,
    sum those stems with ffmpeg amix into mix.wav. Returns the output
    path on success, or None when there's nothing to mix.

    Returns the existing single stem path (no ffmpeg) if exactly one
    stem was selected -- copying it to mix.wav would be 30 MB of
    duplicate data. The caller uses the returned path's name for the
    download URL, so a single-stem selection points the Download Mix
    button directly at the existing stem file.

    amix normalize=0 keeps stem amplitudes as-is; a look-ahead limiter catches
    only peaks above the configured output ceiling without make-up gain."""
    selected = [s for s in job.selected_stems if s in found]
    if not selected:
        return None
    if len(selected) == 1:
        return stems_dir / f"{selected[0]}.wav"
    inputs = [stems_dir / f"{name}.wav" for name in selected]
    out = stems_dir / "mix.wav"
    wav_codec = wav_codec_for_quality_preset(job.quality_preset)
    cmd: list[str] = [
        ffmpeg_executable(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
    ]
    for p in inputs:
        cmd += ["-i", str(p)]
    filter_inputs = "".join(f"[{i}:a]" for i in range(len(inputs)))
    cmd += [
        "-filter_complex",
        f"{filter_inputs}amix=inputs={len(inputs)}:normalize=0,{output_limiter_filter()}",
        "-c:a",
        wav_codec,
        str(out),
    ]
    return out if _run_ffmpeg(job, cmd) else None


_PEAK_POINTS = 1500  # matches OVERVIEW_WAVE_POINTS in player.js


def compute_stem_peaks(stems_dir: Path, stem_names: list[str]) -> None:
    """Compute and cache [min, max] waveform peaks for each stem.
    Failure is non-fatal — missing peaks.json degrades to client-side decode."""
    peaks: dict[str, list[list[float]]] = {}
    for name in stem_names:
        path = stems_dir / f"{name}.wav"
        if not path.is_file():
            continue
        try:
            result: list[list[float]] = []
            with sf.SoundFile(path) as audio:
                if audio.frames <= 0:
                    continue
                # Ceil division preserves the tail instead of producing >1500
                # buckets and truncating the end of the waveform.
                chunk = max(1, (audio.frames + _PEAK_POINTS - 1) // _PEAK_POINTS)
                while len(result) < _PEAK_POINTS:
                    points_left = _PEAK_POINTS - len(result)
                    bins_to_read = min(64, points_left)
                    block = audio.read(
                        chunk * bins_to_read,
                        dtype="float32",
                        always_2d=True,
                    )
                    if block.size == 0:
                        break
                    full_bins = len(block) // chunk
                    if full_bins:
                        shaped = block[: full_bins * chunk].reshape(
                            full_bins,
                            chunk,
                            block.shape[1],
                        )
                        minima = np.minimum(np.min(shaped, axis=(1, 2)), 0.0)
                        maxima = np.maximum(np.max(shaped, axis=(1, 2)), 0.0)
                        result.extend(
                            [float(mn), float(mx)]
                            for mn, mx in zip(minima, maxima, strict=False)
                        )
                    remainder = block[full_bins * chunk :]
                    if remainder.size and len(result) < _PEAK_POINTS:
                        result.append(
                            [
                                float(np.min(remainder, initial=0.0)),
                                float(np.max(remainder, initial=0.0)),
                            ]
                        )
            if result:
                peaks[name] = result
        except Exception:
            logger.warning("could not compute peaks for %s/%s", stems_dir.name, name, exc_info=True)

    if not peaks:
        return

    try:
        tmp = stems_dir / "peaks.json.tmp"
        tmp.write_text(json.dumps(peaks), encoding="utf-8")
        tmp.replace(stems_dir / "peaks.json")
    except Exception:
        logger.warning("could not write peaks.json for %s", stems_dir.name, exc_info=True)


def sweep_old_jobs(jobs_dir: Path) -> None:
    """Delete job directories older than JOB_TTL_SECONDS and remove them from
    the in-memory registry. Called hourly from the background sweep loop
    started at app startup.

    Prefers Job.created_at over directory mtime (which can be touched by
    unrelated filesystem events), and never deletes the directory of an
    active (non-terminal) registered job even if its timestamp looks old.
    Falls back to mtime for orphan directories left over from a previous
    server run, since the registry is in-memory only."""
    cutoff = time.time() - JOB_TTL_SECONDS
    if not jobs_dir.is_dir():
        return
    jobs = registry_all()
    removed = False
    for d in jobs_dir.iterdir():
        if not d.is_dir():
            continue
        job = jobs.get(d.name)
        if job is not None:
            if job.status not in _TERMINAL:
                continue  # never delete an active job's working dir
            if job.created_at >= cutoff:
                continue
        elif d.stat().st_mtime >= cutoff:
            continue
        _rmtree(d)
        registry_remove(d.name)
        removed = True
    for job_id, job in jobs.items():
        if job.status in _TERMINAL and job.created_at < cutoff and not (jobs_dir / job_id).exists():
            registry_remove(job_id)
            removed = True
    if removed:
        registry_persist(jobs_dir)
