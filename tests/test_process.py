from __future__ import annotations

import subprocess
import sys

import pytest

from app.core.models import Job
from app.core.registry import get_proc
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
