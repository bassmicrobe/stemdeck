from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.models import Job, JobCancelled
from app.core.registry import _jobs
from app.pipeline.runner import _prepare_demucs_source, run_local_pipeline, run_pipeline


@pytest.mark.asyncio
async def test_pipeline_transitions_to_error_on_stage_failure(tmp_path: Path):
    job = Job(id="abcdefabcdef")

    def boom(*args, **kwargs):
        raise RuntimeError("download blew up")

    with patch("app.pipeline.runner._run_blocking", side_effect=boom):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "error"
    assert job.error  # generic message returned to client; detail is in server logs


@pytest.mark.asyncio
async def test_pipeline_marks_done_on_success(tmp_path: Path):
    job = Job(id="abcdefabcdee")

    with patch("app.pipeline.runner._run_blocking", return_value=None):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "done"
    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_pipeline_handles_jobcancelled(tmp_path: Path):
    job = Job(id="abcdefabcdec")
    job.cancel_requested = True

    def cancel(*args, **kwargs):
        raise JobCancelled()

    with patch("app.pipeline.runner._run_blocking", side_effect=cancel):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "cancelled"
    # Partial job dir is removed.
    assert not (tmp_path / job.id).exists()


@pytest.mark.asyncio
async def test_pipeline_handles_wrapped_cancel(tmp_path: Path):
    """yt-dlp wraps hook exceptions in DownloadError; the runner must still
    treat it as a cancel when the flag is set."""
    job = Job(id="abcdefabcdeb")
    job.cancel_requested = True

    def wrapped(*args, **kwargs):
        raise RuntimeError("yt-dlp DownloadError wrapping JobCancelled")

    with patch("app.pipeline.runner._run_blocking", side_effect=wrapped):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_pipeline_recovers_from_mkdir_failure(tmp_path: Path):
    """If something pre-lock raises, the job must transition to error
    instead of staying stuck on `queued`."""
    job = Job(id="abcdefabcdea")
    bad_jobs_dir = tmp_path / "blocked"
    # Make jobs_dir a regular file so mkdir(parents=True) under it raises.
    bad_jobs_dir.write_bytes(b"not a directory")

    await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", bad_jobs_dir)

    assert job.status == "error"


@pytest.mark.asyncio
async def test_pipeline_error_cleans_up_job_dir(tmp_path: Path):
    """#82: failed pipeline must remove the job directory so no orphan is left."""
    job = Job(id="abcdefabcde9")

    def boom(*args, **kwargs):
        raise RuntimeError("ffmpeg died")

    with patch("app.pipeline.runner._run_blocking", side_effect=boom):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "error"
    assert not (tmp_path / job.id).exists(), "job dir should be removed on error"


@pytest.mark.asyncio
async def test_pipeline_error_calls_persist(tmp_path: Path):
    """#83: persist is called after an error so the registry stays consistent."""
    job = Job(id="abcdefabcde8")
    _jobs[job.id] = job
    persist_calls = []

    def boom(*args, **kwargs):
        raise RuntimeError("separated badly")

    def fake_persist(jobs_dir):
        persist_calls.append(jobs_dir)

    with (
        patch("app.pipeline.runner._run_blocking", side_effect=boom),
        patch("app.pipeline.runner.persist_registry", side_effect=fake_persist),
    ):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "error"
    assert len(persist_calls) == 1


@pytest.mark.asyncio
async def test_local_pipeline_error_cleans_up_job_dir(tmp_path: Path):
    """#82: local upload error path also removes the job directory."""
    job = Job(id="abcdefabcde7")
    job_dir = tmp_path / job.id
    job_dir.mkdir(parents=True)
    source = job_dir / "source.mp3"
    source.write_bytes(b"ID3")

    def boom(*args, **kwargs):
        raise RuntimeError("demucs blew up")

    with patch("app.pipeline.runner._run_local_blocking", side_effect=boom):
        await run_local_pipeline(job, source, tmp_path)

    assert job.status == "error"
    assert not (tmp_path / job.id).exists(), "job dir should be removed on local error"


def test_prepare_demucs_source_creates_pregain_working_copy(tmp_path: Path, monkeypatch):
    import app.pipeline.runner as runner

    job = Job(id="abcdefabcde6")
    job.quality_preset = "high"
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    calls = []

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        Path(cmd[-1]).write_bytes(b"processed")
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    dest = _prepare_demucs_source(job, source, tmp_path)

    assert dest == tmp_path / "source.demucs.wav"
    assert dest.read_bytes() == b"processed"
    cmd, kwargs = calls[0]
    assert cmd[cmd.index("-filter:a") : cmd.index("-filter:a") + 2] == [
        "-filter:a",
        "volume=-6dB",
    ]
    assert cmd[cmd.index("-c:a") : cmd.index("-c:a") + 2] == ["-c:a", "pcm_f32le"]
    assert kwargs["timeout"] == runner.TIMEOUT_FFMPEG
