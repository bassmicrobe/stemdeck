from __future__ import annotations

import subprocess
import sys

import pytest

from app.core.models import Job
from app.core.registry import add_proc, get_proc, get_procs, remove_proc
from app.pipeline.process import run_tracked_process


def test_run_tracked_process_captures_output_and_clears_registry():
    job = Job(id="abcdefabcdef")

    result = run_tracked_process(
        job,
        [sys.executable, "-c", "print('layerlab')"],
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == b"layerlab"
    assert get_proc(job.id) is None


def test_run_tracked_process_kills_timed_out_child():
    job = Job(id="abcdefabcdee")

    with pytest.raises(subprocess.TimeoutExpired):
        run_tracked_process(
            job,
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.05,
        )

    assert get_proc(job.id) is None


def test_registry_tracks_multiple_processes_per_job():
    job_id = "abcdefabcdec"
    first = object()
    second = object()

    add_proc(job_id, first)  # type: ignore[arg-type]
    add_proc(job_id, second)  # type: ignore[arg-type]
    try:
        assert get_procs(job_id) == (first, second)
        assert get_proc(job_id) is second

        remove_proc(job_id, first)  # type: ignore[arg-type]
        assert get_procs(job_id) == (second,)
    finally:
        remove_proc(job_id, first)  # type: ignore[arg-type]
        remove_proc(job_id, second)  # type: ignore[arg-type]

    assert get_procs(job_id) == ()
