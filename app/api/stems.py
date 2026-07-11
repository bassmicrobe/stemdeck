from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import (
    JOB_ID_RE,
    JOBS_DIR,
    STEM_NAMES,
    TIMEOUT_FFMPEG,
    ffmpeg_executable,
    output_limiter_filter,
    wav_codec_for_quality_preset,
)
from app.core.registry import get as registry_get
from app.pipeline.chords import (
    chord_segments_from_metadata,
    chord_segments_to_csv,
    prepare_chord_midi_segments,
    write_chord_midi,
)

logger = logging.getLogger("stemdeck.api")

router = APIRouter(tags=["stems"])

# Stem files served by this endpoint: the 6 demucs stems + two
# pipeline-produced extras. "original" is the re-encoded source song
# (added when the user picked a strict subset), "mix" is the ffmpeg
# amix of the user's selected stems.
_ALLOWED_NAMES = frozenset(STEM_NAMES) | {"original", "mix"}

# Lanes the dynamic mixdown may sum: the 6 stems plus "original" (the complement
# track shown when the user picked a subset). "mix" is excluded -- it is the
# static pre-render this endpoint replaces. Gains are linear; the studio caps a
# lane at 2.0, so this generous bound just rejects abusive values.
_MIXDOWN_NAMES = frozenset(STEM_NAMES) | {"original"}
_MIXDOWN_MAX_GAIN = 4.0

# Output encoders by container/extension, shared by the dynamic mixdown and the
# stems zip. WAV is lossless PCM, FLAC is lossless compressed, MP3 is VBR ~190 kbps.
_ENCODE_ARGS = {
    "wav": ["-c:a", "pcm_s16le"],
    "mp3": ["-q:a", "2"],
    "flac": ["-c:a", "flac"],
}
MIXDOWN_MEDIA_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}


def _mixdown_codec_args(ext: str, job_quality_preset: str | None) -> list[str]:
    if ext == "wav":
        return ["-c:a", wav_codec_for_quality_preset(job_quality_preset), "-f", "wav"]
    return [*_ENCODE_ARGS[ext], "-f", ext]


def _validate_stem_path(job_id: str, name: str):
    """Shared guard: validate job_id, name, job state, and path. Returns resolved Path."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    if name not in _ALLOWED_NAMES:
        raise HTTPException(status_code=404, detail="unknown stem")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    path = (JOBS_DIR / job_id / "stems" / f"{name}.wav").resolve()
    if not path.is_file() or not path.is_relative_to(JOBS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="stem not found")
    return path


async def _stream_ffmpeg(cmd: list[str]):
    """Yield ffmpeg stdout in 64 KB chunks; kill process on client disconnect."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def _render_ffmpeg_temp(cmd: list[str], *, suffix: str) -> Path:
    """Render a seekable file so WAV headers contain a real frame count."""
    fd, tmp = tempfile.mkstemp(prefix="layerlab_export_", suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd[0],
            "-y",
            *cmd[1:],
            str(tmp_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_FFMPEG)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="audio export timed out") from None
        if proc.returncode != 0 or tmp_path.stat().st_size <= 44:
            detail = (stderr or b"").decode("utf-8", errors="replace").strip()
            logger.error("ffmpeg export failed: %s", detail or f"exit {proc.returncode}")
            raise HTTPException(status_code=500, detail="audio export failed")
        return tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


@router.get("/jobs/{job_id}/stems/peaks.json")
async def get_stem_peaks(job_id: str) -> Response:
    """Return pre-computed waveform peaks for all stems."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    path = (JOBS_DIR / job_id / "stems" / "peaks.json").resolve()
    if not path.is_file() or not path.is_relative_to(JOBS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="peaks not found")
    return FileResponse(
        path,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _chord_export_segments(job, style: str, grid: str):
    segments = chord_segments_from_metadata(job.chord_progression)
    if not segments:
        raise HTTPException(status_code=404, detail="chord metadata not found")
    prepared = prepare_chord_midi_segments(segments, style=style, grid=grid)
    if not prepared:
        raise HTTPException(status_code=404, detail="chord metadata not found")
    return prepared


def _chord_variant_suffix(style: str, grid: str, markers: bool = False) -> str:
    parts = []
    if style != "auto":
        parts.append(style)
    if grid != "beat":
        parts.append(grid)
    if markers:
        parts.append("markers")
    return ("_" + "_".join(parts)) if parts else ""


@router.get("/jobs/{job_id}/chords.mid")
async def get_chord_midi(
    job_id: str,
    style: str = Query(default="auto", description="auto, triads, or sevenths"),
    grid: str = Query(default="beat", description="beat or bar"),
    markers: bool = Query(default=False, description="Write chord labels as MIDI marker events"),
) -> FileResponse:
    """Download the estimated beat-grid chord progression as a Standard MIDI file."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    normalized_style = style if style in ("auto", "triads", "sevenths") else "auto"
    normalized_grid = grid if grid in ("beat", "bar") else "beat"
    path = (JOBS_DIR / job_id / "stems" / "chords.mid").resolve()
    default_export = normalized_style == "auto" and normalized_grid == "beat" and not markers
    if default_export and path.is_file() and path.is_relative_to(JOBS_DIR.resolve()):
        return FileResponse(
            path,
            media_type="audio/midi",
            filename=f"{_download_base(job)}_chords.mid",
        )
    segments = _chord_export_segments(job, normalized_style, normalized_grid)
    fd, tmp = tempfile.mkstemp(prefix="layerlab_chords_", suffix=".mid")
    os.close(fd)
    tmp_path = Path(tmp)
    write_chord_midi(
        tmp_path,
        segments,
        bpm=job.bpm,
        title=job.title,
        markers=markers,
        beat_times=job.beat_times,
    )
    suffix = _chord_variant_suffix(normalized_style, normalized_grid, markers)
    return FileResponse(
        tmp_path,
        media_type="audio/midi",
        filename=f"{_download_base(job)}_chords{suffix}.mid",
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )


@router.get("/jobs/{job_id}/chords.csv")
async def get_chord_csv(
    job_id: str,
    style: str = Query(default="auto", description="auto, triads, or sevenths"),
    grid: str = Query(default="beat", description="beat or bar"),
) -> Response:
    """Download the estimated chord progression as a DAW/spreadsheet-friendly CSV."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    normalized_style = style if style in ("auto", "triads", "sevenths") else "auto"
    normalized_grid = grid if grid in ("beat", "bar") else "beat"
    segments = _chord_export_segments(job, normalized_style, normalized_grid)
    suffix = _chord_variant_suffix(normalized_style, normalized_grid)
    filename = f"{_download_base(job)}_chords{suffix}.csv"
    return Response(
        chord_segments_to_csv(segments),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/{job_id}/midi-analysis.json")
async def get_midi_analysis(job_id: str) -> FileResponse:
    """Download music21 validation and harmonic analysis for the chord MIDI."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")
    path = (JOBS_DIR / job_id / "stems" / "midi-analysis.json").resolve()
    if not path.is_file() or not path.is_relative_to(JOBS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="MIDI analysis not found")
    return FileResponse(
        path,
        media_type="application/json; charset=utf-8",
        filename=f"{_download_base(job)}_midi-analysis.json",
    )


@router.api_route("/jobs/{job_id}/stems/{name}.wav", methods=["GET", "HEAD"], response_model=None)
async def get_stem(
    job_id: str,
    name: str,
    start: float | None = Query(default=None, ge=0, description="Trim start in seconds"),
    end: float | None = Query(default=None, gt=0, description="Trim end in seconds"),
) -> FileResponse | StreamingResponse:
    """Download a WAV stem. Optional ?start=&end= trims to a time region."""
    path = _validate_stem_path(job_id, name)
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")

    if start is None and end is None:
        return FileResponse(path, media_type="audio/wav", filename=_stem_download_filename(job, name, "wav"))

    if start is None or end is None or start >= end:
        raise HTTPException(
            status_code=422,
            detail="start and end are both required and start must be less than end",
        )

    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-i",
        str(path),
        "-t",
        str(end - start),
        "-c:a",
        wav_codec_for_quality_preset(job.quality_preset),
        "-f",
        "wav",
    ]
    rendered = await _render_ffmpeg_temp(cmd, suffix=".wav")
    return FileResponse(
        rendered,
        media_type="audio/wav",
        filename=_stem_download_filename(job, name, "wav", region=True),
        background=BackgroundTask(lambda: rendered.unlink(missing_ok=True)),
    )


@router.get("/jobs/{job_id}/stems/{name}.mp3")
async def get_stem_mp3(
    job_id: str,
    name: str,
    start: float | None = Query(default=None, ge=0, description="Trim start in seconds"),
    end: float | None = Query(default=None, gt=0, description="Trim end in seconds"),
) -> StreamingResponse:
    """Stream a stem as MP3 (VBR ~190 kbps). Optional ?start=&end= trims to a time region."""
    path = _validate_stem_path(job_id, name)
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")

    if (start is None) != (end is None) or (start is not None and start >= end):
        raise HTTPException(
            status_code=422,
            detail="start and end are both required and start must be less than end",
        )

    pre_seek = ["-ss", str(start)] if start is not None else []
    post_seek = ["-t", str(end - start)] if start is not None else []

    cmd = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel",
        "error",
        *pre_seek,
        "-i",
        str(path),
        *post_seek,
        "-q:a",
        "2",  # VBR ~190 kbps
        "-f",
        "mp3",
        "pipe:1",
    ]
    return StreamingResponse(
        _stream_ffmpeg(cmd),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{_stem_download_filename(job, name, "mp3", region=start is not None)}"'},
    )


@router.get("/jobs/{job_id}/mixdown.{ext}", response_model=None)
async def get_mixdown(
    job_id: str,
    ext: str,
    stems: str = Query(..., description="Comma-separated lane names to sum"),
    gains: str = Query(..., description="Comma-separated linear gains, parallel to stems"),
    start: float | None = Query(default=None, ge=0, description="Trim start in seconds"),
    end: float | None = Query(default=None, gt=0, description="Trim end in seconds"),
) -> FileResponse | StreamingResponse:
    """Render a fresh mixdown of the given lanes at the given gains. Mirrors
    the studio mixer (per-stem volume, mute, solo) so the
    exported file matches what is heard. The master fader is intentionally not
    applied -- it is a monitoring level, not part of the mix. Optional ?start=&end=
    trims to a loop region."""
    if ext not in ("wav", "mp3", "flac"):
        raise HTTPException(status_code=404, detail="not found")

    names = [s for s in stems.split(",") if s]
    raw_gains = [g for g in gains.split(",") if g]
    if not names or len(names) != len(raw_gains):
        raise HTTPException(
            status_code=422, detail="stems and gains must be non-empty and equal length"
        )
    try:
        parsed_gains = [float(g) for g in raw_gains]
    except ValueError:
        raise HTTPException(status_code=422, detail="gains must be numbers") from None
    if any(g < 0 or g > _MIXDOWN_MAX_GAIN for g in parsed_gains):
        raise HTTPException(status_code=422, detail="gain out of range")
    if not set(names) <= _MIXDOWN_NAMES:
        raise HTTPException(status_code=422, detail="unknown stem requested")
    if (start is None) != (end is None) or (start is not None and start >= end):
        raise HTTPException(
            status_code=422,
            detail="start and end are both required and start must be less than end",
        )

    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")

    # Validates job_id (404), job done (404), and path traversal (404) per stem.
    paths = [_validate_stem_path(job_id, name) for name in names]

    pre_seek = ["-ss", str(start)] if start is not None else []
    post_seek = ["-t", str(end - start)] if start is not None else []

    cmd: list[str] = [ffmpeg_executable(), "-nostdin", "-loglevel", "error"]
    for p in paths:
        cmd += [*pre_seek, "-i", str(p)]
    # Apply each lane's gain, sum without normalization, then catch only peaks
    # above the output ceiling. level=0 prevents automatic make-up gain.
    filters = [f"[{i}:a]volume={g:.6f}[a{i}]" for i, g in enumerate(parsed_gains)]
    n = len(paths)
    if n > 1:
        labels = "".join(f"[a{i}]" for i in range(n))
        filters.append(
            f"{labels}amix=inputs={n}:normalize=0,{output_limiter_filter()}[mix]"
        )
    else:
        filters.append(f"[a0]{output_limiter_filter()}[mix]")
    out_label = "[mix]"
    codec = _mixdown_codec_args(ext, job.quality_preset)
    cmd += ["-filter_complex", ";".join(filters), "-map", out_label, *post_seek, *codec]

    media_type = MIXDOWN_MEDIA_TYPES[ext]
    filename = _mixdown_filename(job, ext, region=start is not None)
    if ext == "wav":
        rendered = await _render_ffmpeg_temp(cmd, suffix=".wav")
        return FileResponse(
            rendered,
            media_type=media_type,
            filename=filename,
            background=BackgroundTask(lambda: rendered.unlink(missing_ok=True)),
        )
    cmd.append("pipe:1")
    return StreamingResponse(
        _stream_ffmpeg(cmd),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_title(title: str | None) -> str:
    """Sanitize a song title into a filename-safe slug (matches the frontend)."""
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", title or "")
    safe = re.sub(r"_{2,}", "_", safe).strip("_")[:80].strip("_")
    return safe or "stems"


def _safe_profile(job) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", job.profile_label())
    safe = re.sub(r"_{2,}", "_", safe).strip("_")[:64].strip("_")
    return safe


def _download_base(job) -> str:
    title = _safe_title(job.title)
    profile = _safe_profile(job)
    return f"{title}_{profile}" if profile else title


def _stem_download_filename(job, name: str, ext: str, region: bool = False) -> str:
    suffix = "_region" if region else ""
    return f"{_download_base(job)}_{name}{suffix}.{ext}"


def _mixdown_filename(job, ext: str, region: bool = False) -> str:
    suffix = "region" if region else "mix"
    return f"{_download_base(job)}_{suffix}.{ext}"


def _stems_zip_filename(job) -> str:
    return f"{_download_base(job)}_stems.zip"


def _profile_manifest(job, stems: list[str], fmt: str) -> str:
    return "\n".join(
        [
            "LayerLab extraction profile",
            f"Title: {job.title or 'Untitled'}",
            f"Job ID: {job.id}",
            f"Profile: {job.profile_label()}",
            f"Quality: {job.quality_preset}",
            f"Clean: {job.stem_denoise_preset}",
            "Stem gate: "
            + (
                f"on ({job.stem_gate_threshold_db:g} dB)"
                if job.stem_gate_applied and job.stem_gate_threshold_db is not None
                else ("on" if job.stem_gate_applied else "off")
            ),
            f"Selected stems: {', '.join(job.profile_stems())}",
            f"Exported stems: {', '.join(stems)}",
            f"Format: {fmt}",
            "",
        ]
    )


def _build_stems_zip(sources: list[tuple[str, Path]], fmt: str, dest: Path, manifest: str) -> None:
    """Blocking: write the stems into a ZIP. WAV files are stored as-is; MP3 and
    FLAC are transcoded per stem via ffmpeg. ZIP_STORED throughout - audio doesn't
    meaningfully compress, and STORED keeps the build fast. Runs in a thread."""
    if fmt == "wav":
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
            for name, p in sources:
                zf.write(p, arcname=f"{name}.wav")
            zf.writestr("LAYERLAB_PROFILE.txt", manifest)
        return
    encode = _ENCODE_ARGS[fmt]
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        for name, p in sources:
            out = os.path.join(td, f"{name}.{fmt}")
            cmd = [
                ffmpeg_executable(),
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(p),
                *encode,
                "-f",
                fmt,
                out,
            ]
            proc = subprocess.run(  # noqa: S603 — list args, no shell, trusted ffmpeg
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT_FFMPEG,
            )
            if proc.returncode != 0:
                tail = proc.stderr[-2000:].decode("utf-8", "replace")
                raise RuntimeError(f"ffmpeg failed for {name}: {tail}")
            zf.write(out, arcname=f"{name}.{fmt}")
        zf.writestr("LAYERLAB_PROFILE.txt", manifest)


@router.get("/jobs/{job_id}/stems/all.zip")
async def get_all_stems_zip(
    job_id: str,
    fmt: str = Query(default="wav", alias="format"),
    stems: str | None = Query(default=None, description="Comma-separated stems; default all"),
) -> FileResponse:
    """Bundle the requested stems into a single ZIP, named after the song.

    `stems` is the active subset selected in the DAW (whitelisted). When omitted,
    every available stem is included."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    if fmt not in ("wav", "mp3", "flac"):
        raise HTTPException(status_code=422, detail="format must be 'wav', 'mp3', or 'flac'")
    job = registry_get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="job not ready")

    # Resolve the requested subset (whitelisted) or fall back to all stems.
    if stems:
        requested = {s for s in stems.split(",") if s}
        if not requested <= set(STEM_NAMES):
            raise HTTPException(status_code=422, detail="unknown stem requested")
        wanted = [name for name in STEM_NAMES if name in requested]
    else:
        wanted = list(STEM_NAMES)

    jobs_root = JOBS_DIR.resolve()
    stems_dir = (JOBS_DIR / job_id / "stems").resolve()
    if not stems_dir.is_dir() or not stems_dir.is_relative_to(jobs_root):
        raise HTTPException(status_code=404, detail="stems not found")

    sources: list[tuple[str, Path]] = []
    for name in wanted:
        p = (stems_dir / f"{name}.wav").resolve()
        if p.is_file() and p.is_relative_to(jobs_root):
            sources.append((name, p))
    if not sources:
        raise HTTPException(status_code=404, detail="no stems found")

    fd, tmp = tempfile.mkstemp(prefix="stemdeck_zip_", suffix=".zip")
    os.close(fd)
    tmp_path = Path(tmp)
    manifest = _profile_manifest(job, [name for name, _ in sources], fmt)
    try:
        await asyncio.to_thread(_build_stems_zip, sources, fmt, tmp_path, manifest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        logger.exception("failed to build stems zip for job %s", job_id)
        raise HTTPException(status_code=500, detail="failed to build archive") from None

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=_stems_zip_filename(job),
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )
