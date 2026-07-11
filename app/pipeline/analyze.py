from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.core.config import JOBS_DIR, MAX_DURATION_SEC, TIMEOUT_ANALYZE, ffmpeg_executable
from app.core.models import Job, JobCancelled, _set
from app.pipeline.beat_tracker import detect_beat_grid
from app.pipeline.process import run_tracked_process
from app.pipeline.progress import set_stage_progress

logger = logging.getLogger("stemdeck.analyze")

# Albrecht-Shanahan key profiles, derived from a corpus of popular music
# (Albrecht & Shanahan, 2013). Critically, the minor profile here weights
# b7 high (3.48) and M7 low (0.81) — the opposite of Temperley/Kostka-Payne,
# which were derived from Bach chorales and bias toward harmonic minor's
# leading tone. Pop/rock uses natural minor: the b7 is the diatonic
# seventh and rings out constantly (e.g. open D in "Come As You Are",
# which is in E minor and uses D as the b7). Values rescaled so that the
# tonic weight is ≈5 to match the prior code's magnitude.
_MAJOR_PROFILE = (
    5.47,
    0.14,
    2.55,
    0.14,
    3.15,
    2.16,
    0.37,
    4.92,
    0.21,
    1.84,
    0.18,
    1.86,
)
_MINOR_PROFILE = (
    5.06,
    0.14,
    2.42,
    2.42,
    0.35,
    1.96,
    0.35,
    4.16,
    2.53,
    0.28,
    2.67,
    0.62,
)
_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# When the best-major and best-minor scores are this close, we prefer
# minor. Pop/rock has a strong minor-mode prior; the algorithm often
# walks toward the relative major because of an ostinato bass note
# (e.g. "Come As You Are" hammers the open D string in an E minor song),
# and minor is the better default when the call is genuinely ambiguous.
_MINOR_TIE_BREAK_FRAC = 0.05
_ANALYSIS_CHUNK_SEC = 180.0
_NEURAL_ANALYSIS_SAMPLE_SEC = 90.0


def _correlate(profile: tuple[float, ...], chroma: list[float], shift: int) -> float:
    n = len(profile)
    rotated = [chroma[(i + shift) % n] for i in range(n)]
    mean_p = sum(profile) / n
    mean_c = sum(rotated) / n
    num = sum((profile[i] - mean_p) * (rotated[i] - mean_c) for i in range(n))
    denom_p = sum((profile[i] - mean_p) ** 2 for i in range(n)) ** 0.5
    denom_c = sum((rotated[i] - mean_c) ** 2 for i in range(n)) ** 0.5
    if denom_p == 0 or denom_c == 0:
        return 0.0
    return num / (denom_p * denom_c)


def _detect_key(chroma_mean: list[float]) -> tuple[str, str, int]:
    """Find the best-matching key by combining profile correlation with
    root prominence. The Pearson correlation alone is fooled by relative
    keys whose diatonic notes happen to overlap with the song's loud
    pitches but whose own tonic is weak (e.g. picking A minor for an
    E-minor song because E is its 5th and D is its 4th). Weighting by
    the candidate root's chroma value forces the algorithm to also
    confirm 'is this proposed tonic actually loud in the audio?'.
    Logs the chroma vector and top-5 candidates for diagnostics.

    Returns (label, scale_name, confidence_pct).
    - label:        e.g. "G# maj"
    - scale_name:   "Major" or "Natural Minor"
    - confidence_pct: 0-100, derived from the gap between the winning
                    candidate and the runner-up, normalized so a clear
                    win ranks high and a near-tie ranks low."""
    raw: list[tuple[float, float, str, int]] = []  # (weighted, pearson, label, root_idx)
    for shift in range(12):
        root_strength = chroma_mean[shift]
        pearson_maj = _correlate(_MAJOR_PROFILE, chroma_mean, shift)
        pearson_min = _correlate(_MINOR_PROFILE, chroma_mean, shift)
        # Multiplicative root weighting. Pearson can be negative; when
        # it is, a low-chroma root makes things less negative (closer to
        # zero), which is actually the desired ordering.
        raw.append((pearson_maj * root_strength, pearson_maj, f"{_PITCHES[shift]} maj", shift))
        raw.append((pearson_min * root_strength, pearson_min, f"{_PITCHES[shift]} min", shift))
    raw.sort(key=lambda x: x[0], reverse=True)

    # Diagnostic log: chroma profile + top 5 candidates with both raw
    # and weighted scores. Lets us see what the algorithm is "hearing".
    chroma_str = ", ".join(f"{_PITCHES[i]}={chroma_mean[i]:.3f}" for i in range(12))
    top5_str = ", ".join(
        f"{label}={weighted:+.3f}(p{pearson:+.2f}*r{chroma_mean[idx]:.2f})"
        for weighted, pearson, label, idx in raw[:5]
    )
    logger.debug("chroma: %s", chroma_str)
    logger.debug("key candidates (top 5): %s", top5_str)

    # Pick best major and best minor for the tie-break, both by the
    # weighted score.
    best_maj = next(c for c in raw if c[2].endswith("maj"))
    best_min = next(c for c in raw if c[2].endswith("min"))

    gap = abs(best_maj[0] - best_min[0])
    threshold = max(abs(best_maj[0]), abs(best_min[0])) * _MINOR_TIE_BREAK_FRAC
    # Near-tie -> prefer minor (pop/rock prior); clear winner -> use it.
    winner = (best_maj if best_maj[0] > best_min[0] else best_min) if gap > threshold else best_min

    # Confidence: gap between the winner and the runner-up that *isn't*
    # the relative major/minor of the winner (those will always be near-
    # ties with the algorithm's profile-correlation approach, so they
    # tell us nothing about real ambiguity). Normalize so a healthy 0.15
    # gap = 100% confident; tiny gap = 0%.
    runner_up = next(c for c in raw if c[2] != winner[2])
    confidence_score = winner[0] - runner_up[0]
    confidence_pct = max(0, min(100, round(confidence_score / 0.15 * 100)))

    label = winner[2]
    scale_name = "Major" if label.endswith("maj") else "Natural Minor"
    return label, scale_name, confidence_pct


def _measure_loudness(y: object, sr: int) -> tuple[float | None, float | None]:
    """Compute integrated loudness (LUFS, BS.1770) and sample peak (dBFS)
    of the loaded mono signal. Returns (lufs, peak_db); either may be
    None on failure or silence. We use sample peak rather than oversampled
    true peak -- the difference is typically <1 dB and not worth the 4x
    resample cost for a display field."""
    import numpy as np

    if y is None or getattr(y, "size", 0) == 0:
        return None, None

    peak_lin = float(np.abs(y).max())
    peak_db = 20.0 * float(np.log10(peak_lin)) if peak_lin > 1e-9 else None

    lufs: float | None = None
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sr)  # BS.1770-4 with default 400ms blocks
        lufs_raw = float(meter.integrated_loudness(y))
        # pyloudnorm returns -inf for silence; surface as None instead so
        # the frontend can hide the field rather than render "-inf LUFS".
        if np.isfinite(lufs_raw):
            lufs = lufs_raw
    except (ImportError, ValueError) as e:
        # ValueError fires if the clip is shorter than the gating window.
        logger.warning("LUFS measurement failed: %s", e)
    return lufs, peak_db


def _load_audio_ffmpeg(
    source: Path,
    sr: int = 22050,
    duration: float = 180.0,
    *,
    start: float = 0.0,
    job: Job | None = None,
) -> tuple[object, int] | None:
    """Decode `source` to a mono float32 numpy array at `sr` via ffmpeg.
    Bypasses librosa's deprecated audioread fallback (which fires a
    FutureWarning on .webm/.m4a/.opus inputs because soundfile can't
    read those directly). Returns (samples, sr) or None on failure."""
    import numpy as np

    # Defence in depth: even though `source` is constructed by the server
    # (never user-typed), confirm it's a real file inside JOBS_DIR before
    # handing it to a subprocess. Belt-and-suspenders against a future
    # caller change that would let a path slip in from elsewhere.
    resolved = source.resolve()
    jobs_resolved = JOBS_DIR.resolve()
    if not resolved.is_file():
        logger.warning("analyze source is not a file: %s", source)
        return None
    if not resolved.is_relative_to(jobs_resolved):
        logger.warning(
            "analyze source escapes JOBS_DIR (%s not under %s)",
            resolved,
            jobs_resolved,
        )
        return None

    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
    ]
    if start > 0:
        cmd += ["-ss", f"{start:g}"]
    cmd += [
        "-i",
        str(resolved),
        "-ac",
        "1",  # mono
        "-ar",
        str(sr),  # resample
        "-f",
        "f32le",  # raw 32-bit float little-endian
        "-t",
        str(duration),  # cap input duration
        "-",  # write to stdout
    ]
    try:
        if job is None:
            proc = subprocess.run(cmd, capture_output=True, check=True, timeout=TIMEOUT_ANALYZE)
            stdout = proc.stdout
        else:
            result = run_tracked_process(job, cmd, timeout=TIMEOUT_ANALYZE)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode,
                    cmd,
                    output=result.stdout,
                    stderr=result.stderr,
                )
            stdout = result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("ffmpeg decode failed for %s: %s", source, e)
        return None
    y = np.frombuffer(stdout, dtype=np.float32)
    if y.size == 0:
        return None
    return y, sr


def compute_stem_presence(stems_dir: Path, selected_stems: list[str]) -> dict[str, int]:
    """Stream each stem WAV, compute full-track RMS, and normalize to 0-100."""
    import numpy as np
    import soundfile as sf

    result: dict[str, int] = {}
    rms_values: dict[str, float] = {}

    for name in selected_stems:
        wav_path = stems_dir / f"{name}.wav"
        if not wav_path.is_file():
            continue
        try:
            sum_squares = 0.0
            sample_count = 0
            with sf.SoundFile(wav_path) as audio:
                while True:
                    block = audio.read(262_144, dtype="float32", always_2d=True)
                    if block.size == 0:
                        break
                    sum_squares += float(np.sum(block * block, dtype=np.float64))
                    sample_count += int(block.size)
            if sample_count:
                rms_values[name] = float(np.sqrt(sum_squares / sample_count))
        except Exception:
            logger.warning("could not measure stem presence for %s", wav_path, exc_info=True)

    if not rms_values:
        return result

    max_rms = max(rms_values.values())
    if max_rms < 1e-9:
        return {name: 0 for name in rms_values}

    for name, rms in rms_values.items():
        result[name] = max(0, min(100, round(rms / max_rms * 100)))

    return result


def _analysis_windows(
    duration_sec: float | None,
    *,
    max_total: float | None = None,
) -> list[tuple[float, float]]:
    total = min(float(duration_sec or _ANALYSIS_CHUNK_SEC), float(MAX_DURATION_SEC))
    total = max(0.0, total)
    if total <= 0:
        return []
    if max_total is not None and max_total > 0 and total > max_total:
        sample_length = max_total / 3.0
        return [
            (0.0, sample_length),
            ((total - sample_length) / 2.0, sample_length),
            (total - sample_length, sample_length),
        ]
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < total:
        length = min(_ANALYSIS_CHUNK_SEC, total - start)
        windows.append((start, length))
        start += length
    return windows


def _merge_beat_times(existing: list[float], incoming: object, offset: float) -> None:
    for raw in incoming:
        beat = float(raw) + offset
        if beat < 0:
            continue
        if existing and beat - existing[-1] < 0.08:
            continue
        existing.append(beat)


def _normalize_quarter_note_grid(
    beat_times: list[float],
    *,
    tempo_hint: float | None,
) -> list[float]:
    """Resolve half-tempo grids and isolated missed quarter notes."""
    import numpy as np

    if len(beat_times) < 3:
        return beat_times
    intervals = np.diff(np.asarray(beat_times, dtype=np.float64))
    median_interval = float(np.median(intervals))
    if median_interval <= 0 or not np.isfinite(median_interval):
        return beat_times

    normalized = [float(beat) for beat in beat_times]
    if tempo_hint is not None and np.isfinite(tempo_hint):
        grid_bpm = 60.0 / median_interval
        ratio = float(tempo_hint) / grid_bpm
        if grid_bpm < 100 and 1.82 <= ratio <= 2.18:
            normalized = []
            for start, end in zip(beat_times, beat_times[1:], strict=False):
                normalized.append(float(start))
                normalized.append((float(start) + float(end)) / 2.0)
            normalized.append(float(beat_times[-1]))

    normalized_intervals = np.diff(np.asarray(normalized, dtype=np.float64))
    normalized_median = float(np.median(normalized_intervals))
    expanded: list[float] = []
    for start, end in zip(normalized, normalized[1:], strict=False):
        expanded.append(round(start, 3))
        gap = end - start
        multiple = int(round(gap / normalized_median))
        per_beat = gap / max(1, multiple)
        if 2 <= multiple <= 4 and abs(per_beat - normalized_median) <= normalized_median * 0.16:
            expanded.extend(
                round(start + (gap * step / multiple), 3)
                for step in range(1, multiple)
            )
    expanded.append(round(normalized[-1], 3))
    return expanded


def analyze(job: Job, source: Path) -> tuple[int | None, str | None]:
    """Best-effort BPM and key detection. On failure, returns (None, None)
    and leaves job fields untouched -- the chips stay as placeholders."""
    logger.info("analyze: entering for job %s, source=%s", job.id, source)
    set_stage_progress(job, "analyze", 0.0, status="analyzing", stage="Analyzing audio...")
    try:
        import librosa
    except ImportError:
        logger.warning("librosa not installed -- skipping BPM/key analysis")
        set_stage_progress(job, "analyze", 1.0, stage="Analysis skipped")
        return None, None

    try:
        import numpy as np

        beat_times_list: list[float] = []
        downbeat_times: list[float] = []
        beat_tracker = "librosa"
        chroma_sum = np.zeros(12, dtype=np.float64)
        chroma_frames = 0
        lufs: float | None = None
        peak_db: float | None = None
        tempo_hints: list[float] = []
        set_stage_progress(job, "analyze", 0.02, stage="Detecting beat grid...")
        neural_grid = detect_beat_grid(job, source)
        if neural_grid is not None:
            beat_times_list = neural_grid.beats
            downbeat_times = neural_grid.downbeats
            beat_tracker = f"{neural_grid.engine}:{neural_grid.model}"
        windows = _analysis_windows(
            job.duration_sec,
            max_total=_NEURAL_ANALYSIS_SAMPLE_SEC if neural_grid is not None else None,
        )
        if not windows:
            return None, None
        analysis_source = (
            neural_grid.analysis_source
            if neural_grid is not None and neural_grid.analysis_source is not None
            else source
        )

        for idx, (start, length) in enumerate(windows):
            if job.cancel_requested:
                raise JobCancelled()
            set_stage_progress(
                job,
                "analyze",
                idx / len(windows),
                status="analyzing",
                stage=f"Analyzing audio {idx + 1}/{len(windows)}...",
            )
            loaded = _load_audio_ffmpeg(
                analysis_source,
                sr=22050,
                duration=length,
                start=start,
                job=job,
            )
            if loaded is None:
                continue
            y, sr = loaded

            # Bound memory by processing long tracks in fixed-size windows.
            if neural_grid is None:
                y_harmonic, y_percussive = librosa.effects.hpss(y)
                _, beat_frames = librosa.beat.beat_track(y=y_percussive, sr=sr)
                chunk_beats = librosa.frames_to_time(beat_frames, sr=sr)
                _merge_beat_times(beat_times_list, chunk_beats, start)
            else:
                # Neural inference already provides the complete beat grid.
                # CQT is robust enough on the mix for this provisional key;
                # the stem-aware chord pass performs the precise harmony work.
                y_harmonic = y
                try:
                    onset = librosa.onset.onset_strength(y=y, sr=sr)
                    tempo_values = librosa.feature.tempo(onset_envelope=onset, sr=sr)
                    if len(tempo_values) and np.isfinite(tempo_values[0]):
                        tempo_hints.append(float(tempo_values[0]))
                except Exception:
                    logger.debug("tempo octave hint unavailable", exc_info=True)

            chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
            if chroma.shape[1]:
                chroma_sum += np.sum(chroma, axis=1, dtype=np.float64)
                chroma_frames += int(chroma.shape[1])

            chunk_lufs, chunk_peak = _measure_loudness(y, sr)
            if lufs is None:
                lufs = chunk_lufs
            if chunk_peak is not None:
                peak_db = chunk_peak if peak_db is None else max(peak_db, chunk_peak)
            if job.cancel_requested:
                raise JobCancelled()

        chroma_mean = (chroma_sum / chroma_frames).tolist() if chroma_frames else [0.0] * 12
        if any(chroma_mean):
            key, scale, key_confidence = _detect_key(chroma_mean)
        else:
            key, scale, key_confidence = None, None, None

        dynamic_range: float | None = None
        if lufs is not None and peak_db is not None:
            dynamic_range = round(peak_db - lufs, 1)

        tempo_hint = float(np.median(tempo_hints)) if tempo_hints else None
        beat_times_list = _normalize_quarter_note_grid(
            beat_times_list,
            tempo_hint=tempo_hint,
        )
        beat_times_list = [round(t, 3) for t in beat_times_list]

        # Use a robust median interval across the full track. This is less
        # sensitive to one bad chunk boundary than averaging per-window tempos.
        bpm: int | None = None
        tempo_stability: int | None = None
        if len(beat_times_list) > 2:
            intervals = np.diff(np.asarray(beat_times_list, dtype=np.float64))
            median_iv = float(np.median(intervals))
            if median_iv > 0:
                usable = intervals[
                    (intervals >= median_iv * 0.55) & (intervals <= median_iv * 1.8)
                ]
                if usable.size:
                    robust_iv = float(np.median(usable))
                    bpm = int(round(60.0 / robust_iv)) if robust_iv > 0 else None
                    cv = float(np.std(usable) / max(float(np.mean(usable)), 1e-9))
                    tempo_stability = max(0, min(100, round((1 - min(cv, 1)) * 100)))

        _set(
            job,
            bpm=bpm,
            key=key,
            scale=scale,
            key_confidence=key_confidence,
            lufs=lufs,
            peak_db=peak_db,
            dynamic_range=dynamic_range,
            tempo_stability=tempo_stability,
            beat_times=beat_times_list,
            downbeat_times=downbeat_times,
            beat_tracker=beat_tracker,
            stage="Analysis complete",
        )
        set_stage_progress(job, "analyze", 1.0, stage="Analysis complete")
        return bpm, key
    except JobCancelled:
        raise
    except Exception as e:
        logger.exception("analyze failed for job %s", job.id)
        set_stage_progress(job, "analyze", 1.0, stage=f"Analysis skipped ({e})")
        return None, None
