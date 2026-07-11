from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator, model_validator

from app.core.config import (
    JOB_ID_RE,
    JOBS_DIR,
    MAX_DURATION_SEC,
    MAX_PENDING_JOBS,
    QUALITY_PRESET,
    available_demucs_devices,
    demucs_device_choice_available,
    ffmpeg_executable,
    ffprobe_executable,
    normalize_demucs_device_choice,
    normalize_quality_preset,
    normalize_stem_denoise_preset,
    resolve_demucs_device_choice,
    stem_names_for_quality_preset,
)
from app.core.files import atomic_write_text
from app.core.joblog import add_job_log
from app.core.models import Job, _set
from app.core.registry import all_jobs as registry_all_jobs
from app.core.registry import get as registry_get
from app.core.registry import get_procs as registry_get_procs
from app.core.registry import persist as registry_persist
from app.core.registry import refresh_queue_positions as registry_refresh_queue_positions
from app.core.registry import register_if_capacity as registry_register_if_capacity
from app.core.registry import remove as registry_remove
from app.pipeline import run_local_pipeline, run_pipeline
from app.pipeline.download import InvalidYouTubeURL, validate_youtube_url
from app.pipeline.process import terminate_process

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("stemdeck.api")

ACTIVE_JOB_STATUSES = frozenset(("queued", "downloading", "analyzing", "separating", "processing"))
_ALLOWED_EXTS = frozenset((".mp3", ".wav", ".flac", ".m4a"))
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_WS_RE = re.compile(r"\s+")
_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_pipeline_tasks: dict[asyncio.Task, Job] = {}


def _sanitize_title(filename: str) -> str:
    """Strip extension, normalize whitespace, cap at 120 chars."""
    stem = Path(filename).stem
    return _WS_RE.sub(" ", stem).strip()[:120]


def _probe_duration_with_ffmpeg(path: Path) -> float:
    """Fallback duration probe for local setups that have ffmpeg but not ffprobe."""
    result = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = f"{result.stderr}\n{result.stdout}"
    if match := _FFMPEG_DURATION_RE.search(output):
        hours, minutes, seconds = match.groups()
        return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
    raise RuntimeError("ffmpeg could not determine duration")


def _probe_duration(path: Path) -> float:
    """Run ffprobe to get file duration in seconds, falling back to ffmpeg."""
    try:
        result = subprocess.run(
            [
                ffprobe_executable(),
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return _probe_duration_with_ffmpeg(path)
    if result.returncode != 0:
        try:
            return _probe_duration_with_ffmpeg(path)
        except Exception as e:
            raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}") from e
    try:
        return float(result.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"ffprobe returned non-numeric duration: {result.stdout!r}") from e


def _check_file_size(file_obj: object) -> int:
    """Seek to end, return size, rewind. Operates on the SpooledTemporaryFile
    backing a starlette UploadFile — synchronous, suitable for to_thread."""
    file_obj.seek(0, 2)  # type: ignore[union-attr]
    size = file_obj.tell()  # type: ignore[union-attr]
    file_obj.seek(0)  # type: ignore[union-attr]
    return size


def _copy_to_dest(src_file: object, dest: Path) -> None:
    """Copy SpooledTemporaryFile contents to dest. Synchronous, run in thread."""
    with dest.open("wb") as out:
        shutil.copyfileobj(src_file, out)  # type: ignore[arg-type]


def _rmtree_job(job_id: str) -> None:
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        return
    try:
        shutil.rmtree(job_dir)
    except Exception:
        logger.warning("failed to remove job dir %s", job_dir, exc_info=True)


def _task_error_cb(task: asyncio.Task) -> None:
    _pipeline_tasks.pop(task, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("pipeline task raised unhandled exception", exc_info=exc)


def _track_pipeline_task(task: asyncio.Task, job: Job) -> None:
    _pipeline_tasks[task] = job
    task.add_done_callback(_task_error_cb)


async def shutdown_pipeline_tasks() -> None:
    tasks = list(_pipeline_tasks.items())
    for task, job in tasks:
        job.cancel_requested = True
        task.cancel()
    if tasks:
        await asyncio.gather(*(task for task, _ in tasks), return_exceptions=True)


def _selected_stems_for_quality(stems: list[str] | None, quality_preset: str) -> list[str]:
    allowed = stem_names_for_quality_preset(quality_preset)
    selected = [s for s in stems if s in allowed] if stems else list(allowed)
    return selected or list(allowed)


def _device_choice_or_422(value: str | None) -> tuple[str, str]:
    choice = normalize_demucs_device_choice(value)
    if not demucs_device_choice_available(choice):
        available = ", ".join(available_demucs_devices())
        raise HTTPException(
            status_code=422,
            detail=f"Selected device '{choice}' is not available on this machine. Available: {available}",
        )
    return choice, resolve_demucs_device_choice(choice)


class JobRequest(BaseModel):
    url: str
    # Subset of stems to include in the post-processing "selected mix"
    # audio file. None = all 6 (no extra mix produced; would equal the
    # original). Unknown stem names are dropped silently rather than
    # rejected, so a future model with extra stems doesn't break older
    # clients pinning the old set.
    stems: list[str] | None = None
    quality_preset: str | None = None
    stem_denoise: str | None = None
    demucs_device: str | None = None


@router.post("")
async def create_job(request: Request) -> dict[str, str]:
    """Submit a YouTube URL (JSON body) or upload an audio file (multipart/form-data)
    to start a stem-separation job. Returns the new job ID."""
    ct = request.headers.get("content-type", "")
    if "multipart/form-data" in ct:
        return await _create_local_job(request)
    return await _create_youtube_job(request)


async def _create_youtube_job(request: Request) -> dict[str, str]:
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {e}") from e
    try:
        payload = JobRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    try:
        url = validate_youtube_url(payload.url)
    except InvalidYouTubeURL as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    quality_preset = normalize_quality_preset(payload.quality_preset or QUALITY_PRESET)
    stem_denoise_preset = normalize_stem_denoise_preset(payload.stem_denoise)
    demucs_device, demucs_device_resolved = _device_choice_or_422(payload.demucs_device)
    selected = _selected_stems_for_quality(payload.stems, quality_preset)

    job = Job(
        id=uuid.uuid4().hex[:12],
        selected_stems=selected,
        quality_preset=quality_preset,
        stem_denoise_preset=stem_denoise_preset,
        demucs_device=demucs_device,
        demucs_device_resolved=demucs_device_resolved,
        source_url=url,
    )
    if not registry_register_if_capacity(job, MAX_PENDING_JOBS):
        raise HTTPException(status_code=503, detail="Server busy, please try again later")
    add_job_log(job, "URL job accepted", stage="queued", progress=0.0)
    task = asyncio.create_task(run_pipeline(job, url, JOBS_DIR))
    _track_pipeline_task(task, job)
    return {"job_id": job.id}


async def _create_local_job(request: Request) -> dict[str, str]:
    # Fast pre-check: if already at capacity, reject before touching disk.
    # The real atomic check happens in register_if_capacity after the upload.
    if sum(1 for j in registry_all_jobs().values() if j.status in ACTIVE_JOB_STATUSES) >= MAX_PENDING_JOBS:
        raise HTTPException(status_code=503, detail="Server busy, please try again later")

    # Quick pre-check on Content-Length to fail fast for obviously oversized
    # uploads without buffering the whole body first.
    cl_header = request.headers.get("content-length")
    if cl_header:
        try:
            if int(cl_header) > _MAX_UPLOAD_BYTES + 4096:
                raise HTTPException(status_code=422, detail="File exceeds 100 MB limit")
        except ValueError:
            pass

    form = await request.form()
    upload = form.get("file")
    stems_raw = form.get("stems", "[]")
    quality_preset = normalize_quality_preset(str(form.get("quality_preset", QUALITY_PRESET)))
    stem_denoise_preset = normalize_stem_denoise_preset(str(form.get("stem_denoise", "off")))
    demucs_device, demucs_device_resolved = _device_choice_or_422(str(form.get("demucs_device", "auto")))

    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status_code=422, detail="No file provided")

    filename: str = getattr(upload, "filename", "") or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}': only .mp3, .wav, .flac, and .m4a are accepted",
        )

    # Validate stems list from form field
    try:
        stems_list = json.loads(stems_raw)
        if not isinstance(stems_list, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        stems_list = []
    selected = _selected_stems_for_quality(stems_list, quality_preset)

    # Check actual file size (SpooledTemporaryFile is already buffered at this
    # point; seek/tell are fast and don't re-read the body).
    file_obj = upload.file  # type: ignore[union-attr]
    file_size = await asyncio.to_thread(_check_file_size, file_obj)
    if file_size == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if file_size > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="File exceeds 100 MB limit")

    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    source_path = job_dir / f"source{ext}"

    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(_copy_to_dest, file_obj, source_path)

        # Duration check before registering the job so a violation leaves no
        # registered job and no leftover directory.
        try:
            duration = await asyncio.to_thread(_probe_duration, source_path)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not read file duration: {e}") from e

        if duration > MAX_DURATION_SEC:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"File is {int(duration // 60)} min — limit is {MAX_DURATION_SEC // 60} min"
                ),
            )
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("failed to store uploaded audio")
        raise HTTPException(status_code=500, detail="Could not store uploaded file") from exc

    title = _sanitize_title(filename)
    local_source_url = f"local:{title}"
    job = Job(
        id=job_id,
        selected_stems=selected,
        quality_preset=quality_preset,
        stem_denoise_preset=stem_denoise_preset,
        demucs_device=demucs_device,
        demucs_device_resolved=demucs_device_resolved,
        title=title,
        duration_sec=duration,
        source_url=local_source_url,
    )
    if not registry_register_if_capacity(job, MAX_PENDING_JOBS):
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=503, detail="Server busy, please try again later")
    add_job_log(job, "Local audio job accepted", stage="queued", progress=0.0)
    task = asyncio.create_task(run_local_pipeline(job, source_path, JOBS_DIR))
    _track_pipeline_task(task, job)
    return {"job_id": job.id}


@router.get("")
def list_jobs() -> list[dict]:
    """List all completed jobs in the library, sorted by creation time."""
    return [
        job.to_state()
        for job in sorted(registry_all_jobs().values(), key=lambda j: j.created_at)
        if job.status == "done"
    ]


@router.get("/active")
def list_active_jobs() -> list[dict]:
    """List queued and running jobs, sorted by creation time."""
    registry_refresh_queue_positions()
    return [
        job.to_state()
        for job in sorted(registry_all_jobs().values(), key=lambda j: j.created_at)
        if job.status in ACTIVE_JOB_STATUSES
    ]


@router.get("/{job_id}")
def get_job(job_id: str) -> dict:
    """Get the current state of a job by ID."""
    job = registry_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_state()


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Request cancellation of a running job. Idempotent for terminal jobs."""
    job = registry_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in ("done", "error", "cancelled"):
        return job.to_state()
    job.cancel_requested = True
    add_job_log(job, "Cancellation requested", level="warning", stage=job.status)
    procs = registry_get_procs(job_id)
    for proc in procs:
        if proc.poll() is None:
            terminate_process(proc)
    if not procs and job.status == "queued":
        _set(job, status="cancelled", stage="Cancelled")
        registry_refresh_queue_positions()
        registry_persist(JOBS_DIR)
    return job.to_state()


_SECTION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class SectionItem(BaseModel):
    id: str
    name: str
    start: float
    end: float
    color: str

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _SECTION_ID_RE.match(v):
            raise ValueError("invalid section id")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return v.strip()[:64] or "Section"

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        if not _COLOR_RE.match(v):
            raise ValueError("invalid color")
        return v

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, v: float) -> float:
        if not (0 <= v < 86400):
            raise ValueError("time out of range")
        return round(v, 3)


class SectionsBody(BaseModel):
    sections: list[SectionItem]

    @field_validator("sections")
    @classmethod
    def _check_count(cls, value: list[SectionItem]) -> list[SectionItem]:
        if len(value) > 200:
            raise ValueError("too many sections")
        return value

    @model_validator(mode="after")
    def _check_ranges(self):
        ids = set()
        for section in self.sections:
            if section.end <= section.start:
                raise ValueError("section end must be after start")
            if section.id in ids:
                raise ValueError("duplicate section id")
            ids.add(section.id)
        return self


@router.patch("/{job_id}/sections")
def update_sections(job_id: str, body: SectionsBody) -> dict:
    """Save named timeline sections once stem audio is available."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done" and not job.audio_ready:
        raise HTTPException(status_code=409, detail="job is not ready")

    validated = [s.model_dump() for s in body.sections]
    job_dir = (JOBS_DIR / job_id).resolve()
    if not job_dir.is_relative_to(JOBS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="job not found")
    meta_path = job_dir / "metadata.json"

    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    meta["sections"] = validated
    try:
        atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
    except OSError as exc:
        logger.exception("failed to write sections for %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="failed to save sections") from exc

    job.sections = validated
    registry_persist(JOBS_DIR)

    return {"job_id": job_id, "sections": validated}


@router.delete("/{job_id}")
def delete_job(job_id: str) -> dict[str, str]:
    """Delete a completed or failed job and remove its stem files from disk."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = registry_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in ("done", "error", "cancelled"):
        raise HTTPException(status_code=409, detail="job is still running")
    _rmtree_job(job_id)
    registry_remove(job_id)
    registry_persist(JOBS_DIR)
    return {"job_id": job_id, "status": "deleted"}
