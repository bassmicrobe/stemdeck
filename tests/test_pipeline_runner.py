from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.models import Job, JobCancelled
from app.core.registry import _jobs
from app.pipeline.runner import (
    _pipeline_lock_files,
    _prepare_demucs_source,
    _prepare_local_source,
    _run_common,
    run_local_pipeline,
    run_pipeline,
)


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
async def test_pipeline_preserves_ready_audio_when_background_analysis_fails(tmp_path: Path):
    job = Job(id="abcdefabcde1")

    def fail_after_audio_ready(job_arg, *_args):
        job_dir = tmp_path / job_arg.id
        stems_dir = job_dir / "stems"
        stems_dir.mkdir(parents=True, exist_ok=True)
        (stems_dir / "vocals.wav").write_bytes(b"ready")
        job_arg.stems = [
            {"name": "vocals", "url": f"/api/jobs/{job_arg.id}/stems/vocals.wav"}
        ]
        job_arg.audio_ready = True
        raise RuntimeError("chord model crashed")

    with patch("app.pipeline.runner._run_blocking", side_effect=fail_after_audio_ready):
        await run_pipeline(job, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert job.status == "done"
    assert job.analysis_ready is True
    assert "preserved" in (job.analysis_error or "")
    assert (tmp_path / job.id / "stems" / "vocals.wav").read_bytes() == b"ready"


@pytest.mark.asyncio
async def test_pipeline_acquisition_can_overlap_between_jobs(tmp_path: Path):
    """Only Demucs is serialized; front-half work for queued songs can overlap."""
    barrier = threading.Barrier(2, timeout=2.0)
    first = Job(id="abcdefabcda1")
    second = Job(id="abcdefabcda2")

    def acquire_together(*args, **kwargs):
        barrier.wait()

    with patch("app.pipeline.runner._run_blocking", side_effect=acquire_together):
        await asyncio.gather(
            run_pipeline(first, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path),
            run_pipeline(second, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path),
        )

    assert first.status == "done"
    assert second.status == "done"


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


def test_machine_lock_uses_one_file_per_concurrency_slot(tmp_path: Path, monkeypatch):
    import app.pipeline.runner as runner

    base = tmp_path / "layerlab.lock"
    monkeypatch.setenv("STEMDECK_PIPELINE_LOCK", str(base))
    monkeypatch.setattr(runner, "PIPELINE_CONCURRENCY", 3)

    assert _pipeline_lock_files() == (
        tmp_path / "layerlab.lock.0",
        tmp_path / "layerlab.lock.1",
        tmp_path / "layerlab.lock.2",
    )


def test_prepare_demucs_source_skips_redundant_normalization_copy(tmp_path: Path):
    job = Job(id="abcdefabcde6")
    job.quality_preset = "high"
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    dest = _prepare_demucs_source(job, source, tmp_path)

    assert dest == source
    assert job.demucs_gain_db == 0.0


def test_prepare_demucs_source_does_not_duplicate_hot_master_decode(
    tmp_path: Path,
):
    job = Job(id="abcdefabcde4", quality_preset="high", lufs=-7.0, peak_db=1.2)
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    dest = _prepare_demucs_source(job, source, tmp_path)

    assert dest == source
    assert job.demucs_gain_db == 0.0


def test_prepare_demucs_source_converts_m4a_once_without_ffprobe(tmp_path: Path, monkeypatch):
    import app.pipeline.runner as runner

    job = Job(id="abcdefabcde3", quality_preset="high")
    source = tmp_path / "source.m4a"
    source.write_bytes(b"m4a")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(job_arg, cmd, **kwargs):
        assert job_arg is job
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"wav")
        return Result()

    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner, "run_tracked_process", fake_run)

    prepared = _prepare_demucs_source(job, source, tmp_path)

    assert prepared == tmp_path / "source.demucs.wav"
    assert len(calls) == 1
    assert calls[0][calls[0].index("-c:a") : calls[0].index("-c:a") + 2] == [
        "-c:a",
        "pcm_f32le",
    ]


def test_prepare_local_source_defers_decode_to_demucs_ffmpeg(tmp_path: Path):
    job = Job(id="abcdefabcde5", quality_preset="high")
    source = tmp_path / "upload.mp3"
    source.write_bytes(b"ID3")
    dest = _prepare_local_source(job, source, tmp_path)

    assert dest == source
    assert source.read_bytes() == b"ID3"


def test_run_common_exposes_audio_before_chord_analysis(tmp_path: Path):
    job = Job(id="abcdefabcde2", selected_stems=["vocals"])
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    vocals = stems_dir / "vocals.wav"
    vocals.write_bytes(b"stem")
    observed = {}

    def fake_chords(job_arg, *_args, **_kwargs):
        observed["audio_ready"] = job_arg.audio_ready
        observed["analysis_ready"] = job_arg.analysis_ready
        observed["stems"] = list(job_arg.stems)
        observed["mix_url"] = job_arg.mix_url

    with (
        patch("app.pipeline.runner.analyze"),
        patch("app.pipeline.runner.separate", return_value=tmp_path / "demucs"),
        patch("app.pipeline.runner.collect", return_value=["vocals"]),
        patch("app.pipeline.runner.restore_demucs_gain"),
        patch("app.pipeline.runner.repair_bass_dropouts", return_value=False),
        patch("app.pipeline.runner.repair_phase_coherence"),
        patch("app.pipeline.runner.denoise_stem_outputs", return_value=False),
        patch("app.pipeline.runner.process_stem_outputs_with_rust", return_value=None),
        patch("app.pipeline.runner.gate_stem_outputs", return_value=False),
        patch("app.pipeline.runner.stabilize_stem_outputs"),
        patch("app.pipeline.runner.compute_stem_presence", return_value={"vocals": 100}),
        patch("app.pipeline.runner.make_original_track", return_value=None),
        patch("app.pipeline.runner.make_selected_mix", return_value=vocals),
        patch("app.pipeline.runner.compute_stem_peaks"),
        patch("app.pipeline.runner.generate_chord_midi", side_effect=fake_chords),
        patch("app.pipeline.runner.cleanup_source"),
    ):
        _run_common(job, source, tmp_path)

    assert observed == {
        "audio_ready": True,
        "analysis_ready": False,
        "stems": [{"name": "vocals", "url": f"/api/jobs/{job.id}/stems/vocals.wav"}],
        "mix_url": f"/api/jobs/{job.id}/stems/vocals.wav",
    }
    assert job.analysis_ready is True
    assert job.audio_ready_at is not None
