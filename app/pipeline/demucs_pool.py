from __future__ import annotations

import json
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.core.config import (
    PIPELINE_CONCURRENCY,
    TIMEOUT_DEMUCS_STALL,
    TIMEOUT_DEMUCS_TOTAL,
    DemucsSettings,
    ffmpeg_executable,
)
from app.core.models import Job, JobCancelled
from app.core.registry import add_proc, remove_proc
from app.pipeline.demucs_protocol import PROTOCOL_PREFIX
from app.pipeline.process import popen_background, terminate_process
from app.pipeline.progress import set_stage_progress

logger = logging.getLogger("stemdeck.demucs_pool")
_EOF = object()


class WorkerUnavailable(RuntimeError):
    """The persistent worker could not start or negotiate its protocol."""


class WorkerJobError(RuntimeError):
    """Inference started but failed; callers must not repeat it via CLI."""


def _worker_environment(device: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    ffmpeg = Path(ffmpeg_executable())
    if ffmpeg.is_file():
        current_path = env.get("PATH", "")
        env["PATH"] = str(ffmpeg.parent) + (os.pathsep + current_path if current_path else "")
    if device == "mps":
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    try:
        import certifi

        env.setdefault("SSL_CERT_FILE", certifi.where())
        env.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ModuleNotFoundError:
        pass
    return env


class PersistentDemucsWorker:
    def __init__(self, model: str, device: str) -> None:
        self.model = model
        self.device = device
        self.busy = True
        self.ready = False
        self.engine = ""
        self.model_count = 1
        self.last_used = time.monotonic()
        self.last_activity = self.last_used
        self.messages: queue.Queue[dict[str, Any] | object] = queue.Queue()
        self.tail: deque[str] = deque(maxlen=40)
        self.proc = popen_background(
            [
                sys.executable,
                "-m",
                "app.pipeline.demucs_worker",
                "--model",
                model,
                "--device",
                device,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_worker_environment(device),
        )
        if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
            terminate_process(self.proc, force=True)
            raise WorkerUnavailable("persistent worker pipes are unavailable")
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw_line in self.proc.stdout:
                self.last_activity = time.monotonic()
                line = raw_line.rstrip()
                if not line.startswith(PROTOCOL_PREFIX):
                    if line:
                        self.tail.append(f"stdout: {line}")
                    continue
                try:
                    message = json.loads(line[len(PROTOCOL_PREFIX) :])
                except json.JSONDecodeError as error:
                    self.messages.put({"event": "protocol_error", "message": str(error)})
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
                else:
                    self.messages.put(
                        {"event": "protocol_error", "message": "event is not an object"}
                    )
        finally:
            self.messages.put(_EOF)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw_line in self.proc.stderr:
            self.last_activity = time.monotonic()
            line = raw_line.rstrip()
            if line:
                self.tail.append(line)

    def _detail(self) -> str:
        return " | ".join(list(self.tail)[-8:]) or "no worker diagnostics"

    def _next_event(
        self,
        job: Job,
        started_at: float,
        *,
        inference_started: bool,
    ) -> dict[str, Any]:
        error_type = WorkerJobError if inference_started else WorkerUnavailable
        while True:
            if job.cancel_requested:
                self.terminate(force=True)
                raise JobCancelled()
            elapsed = time.monotonic() - started_at
            if TIMEOUT_DEMUCS_TOTAL > 0 and elapsed > TIMEOUT_DEMUCS_TOTAL:
                self.terminate(force=True)
                raise error_type("Demucs worker exceeded the total processing timeout")
            if time.monotonic() - self.last_activity > TIMEOUT_DEMUCS_STALL:
                self.terminate(force=True)
                raise error_type("Demucs worker produced no output before the stall timeout")
            try:
                message = self.messages.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise error_type(
                        f"Demucs worker exited with {self.proc.returncode}: {self._detail()}"
                    ) from None
                continue
            if message is _EOF:
                raise error_type(
                    f"Demucs worker closed its protocol stream: {self._detail()}"
                )
            assert isinstance(message, dict)
            return message

    def ensure_ready(self, job: Job) -> None:
        if self.ready:
            return
        started_at = time.monotonic()
        while True:
            message = self._next_event(job, started_at, inference_started=False)
            event = message.get("event")
            if event == "starting":
                set_stage_progress(job, "separate", 0.0, stage="Loading separation model...")
                continue
            if event == "ready":
                if message.get("model") != self.model or message.get("device") != self.device:
                    raise WorkerUnavailable("Demucs worker identity does not match its pool key")
                engine = message.get("engine")
                if not isinstance(engine, str) or not engine:
                    raise WorkerUnavailable("Demucs worker has no engine identifier")
                self.engine = engine
                try:
                    self.model_count = max(1, min(64, int(message.get("modelCount") or 1)))
                except (TypeError, ValueError) as error:
                    raise WorkerUnavailable("Demucs worker model count is invalid") from error
                self.ready = True
                return
            if event in {"error", "protocol_error"}:
                raise WorkerUnavailable(str(message.get("message") or "worker startup failed"))
            raise WorkerUnavailable(f"unknown Demucs startup event: {event!r}")

    def _send(self, payload: dict[str, Any], *, inference_started: bool) -> None:
        if self.proc.stdin is None or self.proc.poll() is not None:
            error_type = WorkerJobError if inference_started else WorkerUnavailable
            raise error_type(f"Demucs worker is not running: {self._detail()}")
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            self.last_activity = time.monotonic()
        except (BrokenPipeError, OSError) as error:
            error_type = WorkerJobError if inference_started else WorkerUnavailable
            raise error_type(f"could not send command to Demucs worker: {error}") from error

    def separate(
        self,
        job: Job,
        source: Path,
        job_dir: Path,
        settings: DemucsSettings,
    ) -> Path:
        request_id = job.id
        payload = {
            "operation": "separate",
            "requestId": request_id,
            "source": str(source.resolve()),
            "jobDir": str(job_dir.resolve()),
            "shifts": settings.shifts,
            "overlap": settings.overlap,
            "segment": settings.segment,
            "jobs": settings.jobs,
            "float32": settings.float32,
            "clipMode": settings.clip_mode,
        }
        self._send(payload, inference_started=False)
        started_at = time.monotonic()
        while True:
            message = self._next_event(job, started_at, inference_started=True)
            if message.get("requestId") not in {None, request_id}:
                raise WorkerJobError("Demucs worker returned a mismatched request identifier")
            event = message.get("event")
            if event == "progress":
                try:
                    raw_fraction = float(message.get("fraction", 0.0))
                    pass_index = max(1, int(message.get("passIndex") or 1))
                    pass_total = max(1, int(message.get("passTotal") or 1))
                    chunk_index = max(0, int(message.get("chunkIndex") or 0))
                    chunk_total = max(1, int(message.get("chunkTotal") or 1))
                except (TypeError, ValueError) as error:
                    raise WorkerJobError(f"invalid Demucs progress event: {error}") from error
                expected_pass_total = max(1, settings.shifts) * self.model_count
                if not math.isfinite(raw_fraction):
                    raise WorkerJobError("Demucs progress fraction is not finite")
                fraction = min(1.0, max(0.0, raw_fraction))
                if pass_total != expected_pass_total or not 1 <= pass_index <= pass_total:
                    raise WorkerJobError("Demucs progress pass count does not match the model")
                if not 0 <= chunk_index <= chunk_total:
                    raise WorkerJobError("Demucs progress chunk index is invalid")
                chunk_percent = round(chunk_index / chunk_total * 100)
                shift_count = max(1, settings.shifts)
                if self.model_count > 1:
                    model_index = min(self.model_count, ((pass_index - 1) // shift_count) + 1)
                    shift_index = ((pass_index - 1) % shift_count) + 1
                    label = f"Separating model {model_index}/{self.model_count}"
                    if settings.shifts > 0:
                        label += f" · shift {shift_index}/{settings.shifts}"
                    label += f" · {chunk_percent}%"
                elif settings.shifts > 1:
                    label = f"Separating shift {pass_index}/{settings.shifts} · {chunk_percent}%"
                else:
                    label = f"Separating {chunk_percent}%"
                set_stage_progress(job, "separate", fraction * 0.98, stage=label)
                continue
            if event == "saving":
                index = max(1, int(message.get("index") or 1))
                total = max(1, int(message.get("total") or 1))
                if index > total:
                    raise WorkerJobError("Demucs saving progress is invalid")
                stem = str(message.get("stem") or "stem")
                set_stage_progress(
                    job,
                    "separate",
                    0.98 + (0.019 * index / total),
                    stage=f"Saving stem {index}/{total}: {stem}",
                )
                continue
            if event == "done":
                output_raw = message.get("output")
                expected = (job_dir / settings.model / source.stem).resolve()
                if not isinstance(output_raw, str) or Path(output_raw).resolve() != expected:
                    raise WorkerJobError("Demucs worker returned an unexpected output path")
                if not expected.is_dir():
                    raise WorkerJobError(f"Demucs output not found at {expected}")
                set_stage_progress(job, "separate", 1.0, stage="Separation complete")
                return expected
            if event in {"error", "protocol_error"}:
                raise WorkerJobError(str(message.get("message") or "Demucs inference failed"))
            raise WorkerJobError(f"unknown Demucs worker event: {event!r}")

    def terminate(self, *, force: bool = False) -> None:
        if self.proc.poll() is not None:
            return
        terminate_process(self.proc, force=force)
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            terminate_process(self.proc, force=True)

    def shutdown(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            self._send({"operation": "shutdown"}, inference_started=False)
            self.proc.wait(timeout=3)
        except (RuntimeError, subprocess.TimeoutExpired):
            self.terminate(force=True)


class DemucsWorkerPool:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._workers: list[PersistentDemucsWorker] = []
        self._starting = 0
        self._stopping = False

    @property
    def capacity(self) -> int:
        return max(1, PIPELINE_CONCURRENCY)

    def _capacity_for(self, device: str) -> int:
        return self.capacity if device == "cpu" else 1

    def acquire(self, job: Job, model: str, device: str) -> PersistentDemucsWorker:
        while True:
            victim: PersistentDemucsWorker | None = None
            should_start = False
            capacity = self._capacity_for(device)
            with self._condition:
                self._workers = [worker for worker in self._workers if worker.proc.poll() is None]
                if self._stopping:
                    raise WorkerUnavailable("Demucs worker pool is shutting down")
                for worker in self._workers:
                    if not worker.busy and worker.model == model and worker.device == device:
                        worker.busy = True
                        add_proc(job.id, worker.proc)
                        return worker
                idle = [worker for worker in self._workers if not worker.busy]
                if len(self._workers) + self._starting >= capacity and idle:
                    victim = min(idle, key=lambda item: item.last_used)
                    self._workers.remove(victim)
                elif len(self._workers) + self._starting < capacity:
                    self._starting += 1
                    should_start = True
                else:
                    if job.cancel_requested:
                        raise JobCancelled()
                    self._condition.wait(timeout=0.5)
                    continue
            if victim is not None:
                victim.shutdown()
                continue
            if should_start:
                try:
                    worker = PersistentDemucsWorker(model, device)
                except Exception as error:
                    with self._condition:
                        self._starting -= 1
                        self._condition.notify_all()
                    if isinstance(error, WorkerUnavailable):
                        raise
                    raise WorkerUnavailable(f"could not start Demucs worker: {error}") from error
                with self._condition:
                    self._starting -= 1
                    stopping = self._stopping
                    if not stopping:
                        self._workers.append(worker)
                    self._condition.notify_all()
                if stopping:
                    worker.shutdown()
                    raise WorkerUnavailable("Demucs worker pool stopped during startup")
                add_proc(job.id, worker.proc)
                try:
                    worker.ensure_ready(job)
                except Exception:
                    remove_proc(job.id, worker.proc)
                    self.discard(worker)
                    raise
                return worker

    def release(self, worker: PersistentDemucsWorker, *, healthy: bool) -> None:
        if not healthy or worker.proc.poll() is not None:
            self.discard(worker)
            return
        with self._condition:
            worker.busy = False
            worker.last_used = time.monotonic()
            self._condition.notify_all()

    def discard(self, worker: PersistentDemucsWorker) -> None:
        with self._condition:
            if worker in self._workers:
                self._workers.remove(worker)
            self._condition.notify_all()
        worker.terminate(force=True)

    def status(self) -> dict[str, object]:
        with self._condition:
            alive = [worker for worker in self._workers if worker.proc.poll() is None]
            return {
                "capacity": self.capacity,
                "workers": len(alive),
                "busy": sum(1 for worker in alive if worker.busy),
                "models": sorted({f"{worker.model}:{worker.device}" for worker in alive}),
                "stopped": self._stopping,
            }

    def shutdown(self) -> None:
        with self._condition:
            self._stopping = True
            while self._starting:
                self._condition.wait(timeout=0.5)
            workers = list(self._workers)
            self._workers.clear()
            self._condition.notify_all()
        for worker in workers:
            worker.shutdown()


_POOL = DemucsWorkerPool()


def separate_with_worker(
    job: Job,
    source: Path,
    job_dir: Path,
    settings: DemucsSettings,
    device: str,
) -> Path:
    worker = _POOL.acquire(job, settings.model, device)
    healthy = False
    try:
        output = worker.separate(job, source, job_dir, settings)
        healthy = worker.proc.poll() is None
        return output
    finally:
        remove_proc(job.id, worker.proc)
        _POOL.release(worker, healthy=healthy)


def demucs_worker_status() -> dict[str, object]:
    return _POOL.status()


def shutdown_demucs_workers() -> None:
    _POOL.shutdown()
