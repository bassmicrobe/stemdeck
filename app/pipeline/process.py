from __future__ import annotations

import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import BinaryIO

from app.core.config import (
    BACKGROUND_CPU_THREADS,
    BACKGROUND_PROCESS_NICE,
    BACKGROUND_PROCESS_PRIORITY,
)
from app.core.models import Job, JobCancelled
from app.core.registry import add_proc, remove_proc

logger = logging.getLogger("stemdeck.pipeline.process")


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def background_process_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for long-running local audio workers.

    Restricting BLAS/OpenMP worker counts preserves UI responsiveness while the
    extraction still runs in a separate process. Existing explicit env choices
    win, so power users can override these knobs.
    """
    env = dict(base or os.environ)
    threads = str(max(1, int(BACKGROUND_CPU_THREADS)))
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "TORCH_NUM_THREADS",
    ):
        env.setdefault(name, threads)
    return env


def lower_process_priority(proc: subprocess.Popen) -> None:
    """Best-effort background priority for a child process.

    On macOS/Linux we can renice the child from the parent without using
    preexec_fn, avoiding the usual thread-safety caveat. On platforms where
    this is unavailable, silently keep the normal priority.
    """
    if not BACKGROUND_PROCESS_PRIORITY:
        return
    try:
        if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, proc.pid, BACKGROUND_PROCESS_NICE)
    except Exception:
        logger.debug("could not lower process priority for pid %s", proc.pid, exc_info=True)


def popen_background(
    cmd: list[str],
    *,
    stdout: int | BinaryIO | None = None,
    stderr: int | BinaryIO | None = None,
    text: bool = False,
    bufsize: int = -1,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    kwargs: dict[str, object] = {
        "stdout": stdout,
        "stderr": stderr,
        "text": text,
        "bufsize": bufsize,
        "env": background_process_env(env),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    lower_process_priority(proc)
    return proc


def terminate_process(proc: subprocess.Popen, *, force: bool = False) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt" and os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            proc.kill()
        else:
            proc.terminate()
    except OSError:
        if proc.poll() is None:
            try:
                proc.kill() if force else proc.terminate()
            except OSError:
                pass


def run_tracked_process(
    job: Job,
    cmd: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    proc = popen_background(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    add_proc(job.id, proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process(proc, force=True)
            stdout, stderr = proc.communicate()
            raise
    finally:
        remove_proc(job.id, proc)
    if job.cancel_requested:
        raise JobCancelled()
    return ProcessResult(
        proc.returncode or 0,
        stdout if isinstance(stdout, bytes) else b"",
        stderr if isinstance(stderr, bytes) else b"",
    )
