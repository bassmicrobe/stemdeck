from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from importlib import resources
from pathlib import Path

from app.core.config import (
    DEMUCS_DEVICE,
    TIMEOUT_DEMUCS_STALL,
    TIMEOUT_DEMUCS_TOTAL,
    DemucsSettings,
    demucs_settings_for_preset,
    ffmpeg_executable,
)
from app.core.models import Job, JobCancelled
from app.core.registry import add_proc, remove_proc
from app.pipeline.process import popen_background, terminate_process
from app.pipeline.progress import set_stage_progress

logger = logging.getLogger("stemdeck.pipeline")

_PCT_RE = re.compile(r"(\d{1,3})%")
_DOWNLOAD_RATE_RE = re.compile(r"(?:[KMGT]?i?B/s|[KMGT]?i?B\b)", re.IGNORECASE)
# Terminate demucs if stderr produces no output for this many seconds.
# GPU processing can be silent for minutes; 30 min covers legitimate pauses
# while still catching genuine hangs (GPU deadlock, OOM stall, etc.).


def demucs_model_count(model_name: str) -> int:
    """Return the number of sub-models in a packaged Demucs bag."""
    try:
        text = resources.files("demucs.remote").joinpath(f"{model_name}.yaml").read_text()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return 1
    match = re.search(r"^models:\s*\[(.+)]\s*$", text, re.MULTILINE)
    if not match:
        return 1
    return max(1, len(re.findall(r"['\"][^'\"]+['\"]", match.group(1))))


class DemucsProgress:
    """Convert Demucs' per-shift progress bars into one monotonic fraction."""

    def __init__(self, shifts: int, model_count: int = 1) -> None:
        self.shifts = max(1, shifts)
        self.model_count = max(1, model_count)
        self.total_passes = self.shifts * self.model_count
        self.pass_index = 0
        self.last_percent = 0

    def update(self, percent: int, line: str = "") -> tuple[float, str]:
        percent = max(0, min(100, percent))
        # Model downloads also use tqdm percentages. Do not count those as an
        # inference pass when the weights are fetched on first use.
        if _DOWNLOAD_RATE_RE.search(line):
            return 0.0, "Preparing separation model..."
        if (
            self.total_passes > 1
            and self.last_percent >= 80
            and percent <= 20
            and self.pass_index < self.total_passes - 1
        ):
            self.pass_index += 1
        self.last_percent = percent
        fraction = min(1.0, (self.pass_index + (percent / 100.0)) / self.total_passes)
        if self.model_count == 1 and self.shifts == 1:
            label = f"Separating {percent}%"
        elif self.model_count == 1:
            label = (
                f"Separating shift {self.pass_index + 1}/{self.shifts}"
                f" · {percent}%"
            )
        else:
            model_index = min(self.model_count - 1, self.pass_index // self.shifts)
            shift_index = self.pass_index % self.shifts
            label = (
                f"Separating model {model_index + 1}/{self.model_count}"
                f" · shift {shift_index + 1}/{self.shifts}"
                f" · {percent}%"
            )
        return fraction, label


def build_demucs_command(
    source: Path,
    job_dir: Path,
    settings: DemucsSettings,
    device: str | None = None,
) -> list[str]:
    resolved_device = device or DEMUCS_DEVICE
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "-n",
        settings.model,
        "-d",
        resolved_device,
    ]
    # Demucs defaults to shifts=1 and overlap=0.25. Always pass LayerLab's
    # values so the selected preset matches the actual inference workload.
    cmd += ["--shifts", str(max(0, settings.shifts))]
    cmd += ["--overlap", f"{min(0.99, max(0.001, settings.overlap)):g}"]
    if settings.segment > 0:
        # Demucs 4's CLI parser accepts integer seconds even though the Python
        # API represents segment duration as a float.
        cmd += ["--segment", str(max(1, int(settings.segment)))]
    if resolved_device == "cpu" and settings.jobs > 0:
        cmd += ["-j", str(settings.jobs)]
    if settings.float32:
        cmd.append("--float32")
    if settings.clip_mode:
        cmd += ["--clip-mode", settings.clip_mode]
    cmd += ["-o", str(job_dir), str(source)]
    return cmd


def separate(job: Job, source: Path, job_dir: Path) -> Path:
    set_stage_progress(job, "separate", 0.0, status="separating", stage="Separating stems...")

    resolved_device = job.demucs_device_resolved or DEMUCS_DEVICE
    settings = demucs_settings_for_preset(job.quality_preset, device=resolved_device)
    cmd = build_demucs_command(source, job_dir, settings, resolved_device)
    env = os.environ.copy()
    ffmpeg = Path(ffmpeg_executable())
    if ffmpeg.is_file():
        path = env.get("PATH", "")
        env["PATH"] = str(ffmpeg.parent) + (os.pathsep + path if path else "")
    if resolved_device == "mps":
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    try:
        import certifi

        env.setdefault("SSL_CERT_FILE", certifi.where())
        env.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ModuleNotFoundError:
        pass

    proc = popen_background(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0,
        env=env,
    )
    if proc.stderr is None:
        raise RuntimeError("demucs subprocess has no stderr pipe")
    add_proc(job.id, proc)

    # tqdm uses \r to redraw -- read char-by-char and split on \r or \n.
    # Keep the last few non-progress lines so we can surface them if demucs
    # exits non-zero (otherwise the only signal would be a bare exit code).
    buf = ""
    tail: list[str] = []
    last_output: list[float] = [time.monotonic()]
    started_at = time.monotonic()
    watchdog_reason: list[str | None] = [None]
    progress = DemucsProgress(settings.shifts, demucs_model_count(settings.model))
    # Event set by the reader loop when the process exits normally so the
    # watchdog can wake up immediately instead of waiting out its 30 s sleep.
    _done_evt = threading.Event()

    def _watchdog() -> None:
        while not _done_evt.wait(timeout=30):
            if proc.poll() is not None:
                return
            if (
                TIMEOUT_DEMUCS_TOTAL > 0
                and time.monotonic() - started_at > TIMEOUT_DEMUCS_TOTAL
            ):
                watchdog_reason[0] = "total"
                logger.warning(
                    "demucs exceeded total timeout of %ss, terminating job %s",
                    TIMEOUT_DEMUCS_TOTAL,
                    job.id,
                )
                terminate_process(proc)
                return
            if time.monotonic() - last_output[0] > TIMEOUT_DEMUCS_STALL:
                watchdog_reason[0] = "stall"
                logger.warning(
                    "demucs stalled for %ss with no output, terminating job %s",
                    TIMEOUT_DEMUCS_STALL,
                    job.id,
                )
                terminate_process(proc)
                return

    wt = threading.Thread(target=_watchdog, daemon=True)
    wt.start()
    try:
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            last_output[0] = time.monotonic()
            if ch in ("\r", "\n"):
                line = buf.strip()
                buf = ""
                if not line:
                    continue
                m = _PCT_RE.search(line)
                if m:
                    pct = max(0, min(100, int(m.group(1))))
                    fraction, label = progress.update(pct, line)
                    set_stage_progress(job, "separate", fraction, stage=label)
                else:
                    tail.append(line)
                    if len(tail) > 40:
                        tail.pop(0)
            else:
                buf += ch

        proc.wait()
    finally:
        _done_evt.set()
        remove_proc(job.id, proc)
        wt.join(timeout=2)

    # POST /cancel calls proc.terminate() directly, which causes the read loop
    # above to hit EOF and proc.wait() to return a nonzero status. Translate
    # that into JobCancelled before the generic "demucs failed" path.
    if job.cancel_requested:
        raise JobCancelled()
    if watchdog_reason[0] == "total":
        hours = TIMEOUT_DEMUCS_TOTAL / 3600
        raise RuntimeError(f"demucs exceeded the {hours:g}-hour total processing limit")
    if watchdog_reason[0] == "stall":
        minutes = TIMEOUT_DEMUCS_STALL / 60
        raise RuntimeError(f"demucs produced no progress output for {minutes:g} minutes")
    if proc.returncode != 0:
        detail = "\n".join(tail[-15:]) if tail else "(no stderr captured)"
        logger.error("[%s] demucs exited %s; tail:\n%s", job.id, proc.returncode, detail)
        last = tail[-1] if tail else f"exit status {proc.returncode}"
        raise RuntimeError(f"demucs failed: {last}")

    stems_root = job_dir / settings.model / source.stem
    if not stems_root.is_dir():
        raise RuntimeError(f"demucs output not found at {stems_root}")
    set_stage_progress(job, "separate", 1.0, stage="Separation complete")
    return stems_root
